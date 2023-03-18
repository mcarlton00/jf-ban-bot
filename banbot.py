import requests
import simplematrixbotlib as botlib
import threading
import queue
from discord.ext import tasks
import asyncio
from fernet_wrapper import Wrapper as fw
import time
import configparser


class discord_thread(threading.Thread):

    def __init__(self, discord_token):
        super(discord_thread, self).__init__()

        self.discord_token = discord_token

    def run(self):
        '''
        discordpy doesn't like being in it's own thread, need the import to
        inside the thread
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
        #print('checking user queue')
        if not user_queue.empty():
            user = user_queue.get()
            # Clear username of bridge boilerplate
            user_id = user.replace('@_discord_', '').replace(':t2bot.io', '')
            '''
            Get the server id.  It's only connected to the jellyfin server,
            so no filtering is needed
            '''
            guild = self.discord_bot.guilds[0]
            # Retrieve data of the offending user and ban them
            member = await guild.fetch_member(user_id)
            await member.ban()


def ban_matrix(homeserver, headers, admin_user, ban_user):

    # Get a list of all rooms the bot is joined to
    r = requests.get(f'{homeserver}/_matrix/client/v3/joined_rooms',
                     headers=headers)
    joined_rooms = r.json().get('joined_rooms', [])

    # Try to ban user from all joined rooms.  Will fail if not modded
    for room in joined_rooms:
        ban_payload = {
            'user_id': ban_user,
            'reason': 'triggered deny list'
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


def kick_matrix(homeserver, headers, admin_user, ban_user):

    # Get a list of all rooms the bot is joined to
    r = requests.get(f'{homeserver}/_matrix/client/v3/joined_rooms',
                     headers=headers)
    joined_rooms = r.json().get('joined_rooms', [])

    # Try to ban user from all joined rooms.  Will fail if not modded
    for room in joined_rooms:
        kick_payload = {
            'user_id': ban_user,
            'reason': 'triggered deny list'
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
    ban_terms = data['ban_terms']
    ban_list = ban_terms.split(',')

    # matrix_bot config, uncomment to join new rooms
    matrix_config = botlib.Config()
    # matrix_config.join_on_invite = True

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

    @matrix_bot.listener.on_message_event
    async def auto_ban(room, message):
        contents = message.body

        # Check each entry from our auto ban list
        for term in ban_list:
            if term in contents:
                sender = message.sender
                print(f'Found {term} in message, banning user {sender}')
                if '@_discord_' in sender:
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
                delete = requests.put(
                    f'{homeserver}/_matrix/client/v3/rooms/{room_id}/redact/{event_id}/{trans_id}',  # noqa:E501
                    json=redact_payload, headers=headers)

    matrix_bot.run()
    #discord.join()
