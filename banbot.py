import time
import configparser
import asyncio
import threading
import queue
import random
import string
import re

import nio
import requests
import simplematrixbotlib as botlib
from discord.ext import tasks
from fernet_wrapper import Wrapper as fw
from expiringdict import ExpiringDict


class discord_thread(threading.Thread):

    def __init__(self, discord_token):
        super(discord_thread, self).__init__()

        self.discord_token = discord_token

    def run(self):
        '''
        discordpy doesn't like being in it's own thread, need the import to
        happen inside the thread
        '''
        import discord

        # Initial discord bot config
        intents = discord.Intents.default()
        intents.bans = True
        intents.members = True
        self.discord_bot = discord.Client(intents=intents)

        # Displays a message on successful startup and starts the queue loop
        @self.discord_bot.event
        async def on_ready():
            print(f'We have logged in as {self.discord_bot.user}')
            self.check_queue.start()

        # Starts the bot
        self.discord_bot.run(self.discord_token)

    '''
    Checks the user queue every 5 seconds in there's a discord user that needs
    to get banned.  Because of the way discordpy works everything needs to be
    self contained instead of calling functions directly from the main thread
    '''
    @tasks.loop(seconds=5)
    async def check_queue(self):
        if not user_queue.empty():
            user = user_queue.get()
            # Clear username of bridge boilerplate
            user_id = user.replace('@jfdiscord_', '').replace(':im.jellyfin.org', '')  # noqa: E501
            '''
            Get the server id.  It's only connected to the jellyfin server,
            so no filtering is needed
            '''
            guild = self.discord_bot.guilds[0]
            # Retrieve data of the offending user and ban them
            member = await guild.fetch_member(user_id)
            await member.ban()


def get_matrix_rooms(homeserver, headers):
    # Get a list of all rooms the bot is joined to
    r = requests.get(f'{homeserver}/_matrix/client/v3/joined_rooms',
                     headers=headers)
    joined_rooms = r.json().get('joined_rooms', [])

    return joined_rooms


def ban_matrix(homeserver, headers, admin_user, ban_user, ban_reason=None):

    # Default ban reason
    if not ban_reason:
        ban_reason = 'Triggered deny list'

    joined_rooms = get_matrix_rooms(homeserver, headers)

    # Try to ban user from all joined rooms.  Will fail if not modded
    for room in joined_rooms:
        ban_payload = {
            'user_id': ban_user,
            'reason': ban_reason
        }
        # Ban the user from the room
        # https://spec.matrix.org/v1.3/client-server-api/#post_matrixclientv3roomsroomidban  # noqa: E501
        try:
            r = requests.post(
                f'{homeserver}/_matrix/client/v3/rooms/{room}/ban',
                json=ban_payload, headers=headers)
            r.raise_for_status()
        except:
            print(f'failed to ban from {room}')


def kick_matrix(homeserver, headers, admin_user, ban_user, ban_reason=None):

    # Default ban reason
    if not ban_reason:
        ban_reason = 'Triggered deny list'

    # Get a list of all rooms the bot is joined to
    r = requests.get(f'{homeserver}/_matrix/client/v3/joined_rooms',
                     headers=headers)
    joined_rooms = r.json().get('joined_rooms', [])

    # Try to ban user from all joined rooms.  Will fail if not modded
    for room in joined_rooms:
        kick_payload = {
            'user_id': ban_user,
            'reason': ban_reason
        }
        # Kick the user from the room
        # https://matrix.org/docs/api/#post-/_matrix/client/v3/rooms/-roomId-/kick  # noqa: E501
        try:
            r = requests.post(
                f'{homeserver}/_matrix/client/v3/rooms/{room}/kick',
                json=kick_payload, headers=headers)
            r.raise_for_status()
        except:
            print(f'failed to ban from {room}')


def delete_user_messages(homeserver, headers, admin_user, ban_user, room_id):
    letters = string.ascii_letters

    # Retrieve previous events in the room
    # https://spec.matrix.org/v1.3/client-server-api/#get_matrixclientv3roomsroomidmessages
    num_events = 50
    r2 = requests.get(
        f'{homeserver}/_matrix/client/v3/rooms/{room_id}/messages?limit={num_events}&dir=b',
        headers=headers)

    events = r2.json().get('chunk', [])
    for event in events:
        # If the user has posted any messages, delete them
        if event.get('sender') == ban_user and event.get('type') == 'm.room.message':
            event_id = event.get('event_id')
            # generate random transaction id
            trans_id = ''.join(random.choice(letters) for _ in range(20))
            redact_payload = {'reason': 'nuked'}
            # https://spec.matrix.org/v1.3/client-server-api/#redactions
            requests.put(
                f'{homeserver}/_matrix/client/v3/rooms/{room_id}/redact/{event_id}/{trans_id}',
                json=redact_payload, headers=headers)


def process_user_rooms(homeserver, headers, admin_user, ban_user, room):

    # Process the room that this was called from first
    delete_user_messages(homeserver, headers, admin_user, ban_user, room.room_id)

    joined_rooms = get_matrix_rooms(homeserver, headers)

    for room in joined_rooms:
        delete_user_messages(homeserver, headers, admin_user, ban_user, room)


def find_ban_user(message):
    '''
    Parses the message to find the username to be banned
    '''
    # Tries to pull it from the mentions for easy matching
    ban_user = message.event.source['content'].get('m.mentions', {}).get('user_ids', [])

    if ban_user:
        # The mentions return a list, we need a string
        print('Found ban user in mentions')
        ban_user = ban_user[0]
    else:
        # If the user isn't in the mentions, parse the formatted body for the name
        # Matches a pattern of "<a href=\"https://foo.bar/#/@BAN_USER:HOMESERVER.TLD\">display name</a>"
        body = message.event.formatted_body
        regex_match = re.search('https://.*\..*/(.*)\\"', body)
        if regex_match:
            ban_user = regex_match.group(1)
            print('Found ban user in body')
        else:
            print(f'Unable to find ban user in {body}')
            ban_user = ""

    return ban_user


# Copied from https://github.com/i10b/simplematrixbotlib/issues/73#issuecomment-969416145  # noqa: E501
class TokenCreds(botlib.Creds):
    def __init__(self, homeserver, username=None, password=None, login_token=None, access_token=None, session_stored_file='session.txt', device_name='banbot'):  # noqa: #501
        super().__init__(homeserver, username, password, login_token, access_token, session_stored_file)  # noqa:E501
        self.device_name = device_name

    def session_write_file(self) -> str:
        self._key = fw.key_from_pass(self.access_token)
        super().session_write_file()
        return self.access_token


async def initialSync(bot) -> str:
    from nio import SyncResponse
    await bot.api.login()
    resp = await bot.api.async_client.sync(timeout=65536, full_state=False)
    if isinstance(resp, SyncResponse):
        print("login and initial sync successful")
        bot.creds.access_token = bot.api.async_client.access_token
        return bot.creds.session_write_file()


if __name__ == '__main__':
    # Used to send usernames to the discord thread
    global user_queue
    user_queue = queue.Queue()

    # Load values from config file
    config = configparser.ConfigParser()
    config.read('config.ini')
    data = config['banbot']

    discord_token = data['discord_token']
    # Spin off discord into it's own thread
    discord = discord_thread(discord_token)
    discord.start()

    # Matrix connection information
    homeserver = data['matrix_homeserver']
    matrix_user = data['matrix_user']
    matrix_pass = data['matrix_password']

    # Retrieve list of auto ban terms, split into list
    message_ban_terms = data['ban_terms']
    message_ban_list = message_ban_terms.split(',')
    name_ban_terms = data['ban_names']
    name_ban_list = name_ban_terms.split(',')

    # Determine if bot will accept room invites
    accept_invites = data['accept_invites']
    matrix_config = botlib.Config()
    matrix_config.join_on_invite = accept_invites

    '''
    Copied from https://github.com/i10b/simplematrixbotlib/issues/73#issuecomment-969416145  # noqa:E501
    Needed so we can catch the matrix access token from the bot framework
    to use for our moderation api calls
    '''
    creds = TokenCreds(homeserver=homeserver,
                       username=matrix_user,
                       password=matrix_pass,
                       session_stored_file='session.txt')
    matrix_bot = botlib.Bot(creds)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    matrix_access_token = loop.run_until_complete(initialSync(matrix_bot))  # noqa:E501
    headers = {'Authorization': f'Bearer {matrix_access_token}'}

    # Get membership of Jellyfin Super Friends room
    r = requests.get(f'{homeserver}/_matrix/client/v3/rooms/!IcxAyGVzujeflEJlQt:matrix.org/joined_members', headers=headers)

    # Formatted user list
    members = r.json().get('joined', {})
    mod_users = list(members.keys())

    # Create a dictionary that automatically cleans it's keys after 30 seconds
    image_cache = ExpiringDict(max_len=100, max_age_seconds=30)

    @matrix_bot.listener.on_message_event
    async def auto_ban(room, message):
        contents = message.body

        # Check each entry from our auto ban list
        for term in message_ban_list:
            if term in contents:
                sender = message.sender
                if sender in mod_users:
                    break

                print(f'Found {term} in message, banning user {sender}')
                if '@jfdiscord_' in sender:
                    # If the sender is a discord user, ban in other thread
                    user_queue.put(sender)
                    '''
                    Then kick them from matrix so they don't show up in
                    the member list anymore
                    '''
                    kick_matrix(homeserver, headers, matrix_user, sender)
                else:
                    # If the sender is a matrix user, ban through matrix api
                    ban_matrix(homeserver, headers, matrix_user, sender)
                '''
                Delete the offending message from the matrix side.  The
                bridge will remove it from discord
                '''
                room_id = room.room_id
                event_id = message.event_id
                trans_id = time.time()
                redact_payload = {'reason': 'triggered deny list'}
                requests.put(
                    f'{homeserver}/_matrix/client/v3/rooms/{room_id}/redact/{event_id}/{trans_id}',  # noqa:E501
                    json=redact_payload, headers=headers)

                break

    @matrix_bot.listener.on_custom_event(nio.RoomMemberEvent)
    async def new_user(room, event):
        # Only trigger when users join a room
        if event.membership == 'join':
            sender = event.sender
            # Loop through the list of banned username terms
            for term in name_ban_list:
                if term in sender:
                    # Ban user if found
                    print(f'Banning user {sender} for violating name rules')
                    ban_matrix(homeserver, headers, matrix_user, sender)

    @matrix_bot.listener.on_message_event
    async def nuke(room, message):
        match = botlib.MessageMatch(room, message, matrix_bot, '!')

        if match.is_not_from_this_bot() and match.prefix() and match.command('nuke'):
            sender = match.event.sender
            print(f'Nuke called by {sender}')
            # check if message sender is in Jellyfin Super Friends
            if sender in mod_users:
                print(f'{sender} is a mod, continuing')
                ban_user = find_ban_user(match)

                # If the target user is a member of Jellyfin Super Friends, don't ban
                if ban_user and ban_user not in mod_users:
                    print(f'Nuking user {ban_user}')
                    if '@jfdiscord_' in ban_user:
                        # If the sender is a discord user, ban in other thread
                        user_queue.put(ban_user)
                        '''
                        Then kick them from matrix so they don't show up in
                        the member list anymore
                        '''
                        kick_matrix(homeserver, headers, matrix_user, ban_user, 'Nuked')
                    else:
                        # If the sender is a matrix user, ban through matrix api
                        ban_matrix(homeserver, headers, matrix_user, ban_user, 'Nuked')

                    print(f'User {ban_user} has been banned, deleting messages')
                    process_user_rooms(homeserver, headers, matrix_user, ban_user, room)
                elif ban_user:
                    print(f'Target user {ban_user} is a mod, cancelling nuke')
            else:
                print(f'Sender {sender} is not a mod, cancelling nuke')

    @matrix_bot.listener.on_custom_event(nio.RoomMessageMedia)
    async def new_message_media(room, event):
        '''
        Triggers when a new image/sound/media source is posted to a room
        '''
        sender = event.sender
        # Check how many pictures this sender has already sent, increment by 1
        count = image_cache.get(sender, 0)
        count += 1
        print(f'{sender} has sent {count} images in the last 30 seconds')
        # If the user has sent more than 5 images in 30 seconds, ban and delete messages
        if count > 7:
            if sender not in mod_users:
                print(f'Nuking user {sender} for image spam')
                if '@jfdiscord_' in sender:
                    # If the sender is a discord user, ban in other thread
                    user_queue.put(sender)
                    '''
                    Then kick them from matrix so they don't show up in
                    the member list anymore
                    '''
                    kick_matrix(homeserver, headers, matrix_user, sender, 'Nuked')
                else:
                    # If the sender is a matrix user, ban through matrix api
                    ban_matrix(homeserver, headers, matrix_user, sender, 'Nuked')

                print(f'User {sender} has been banned, deleting messages')
                process_user_rooms(homeserver, headers, matrix_user, sender, room)
            elif ban_user:
                print(f'Target user {sender} is a mod, cancelling nuke')
        else:
            image_cache[sender] = count

    matrix_bot.run()
