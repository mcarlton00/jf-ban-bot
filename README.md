# JF Ban Bot

Will automatically ban users and remove messages who violate the configured ban list of phrases.  All message handling is done through matrix.  When a violation is detected, the bot determines if the user belongs to matrix or discord and bans them on the appropriate platform and deletes the message from matrix.  The bridge then deletes the message from the discord side.

## Setup

### Discord Bot

Follow the directions from discordpy to set up a bot account.  When generating the invite URL, be sure to select the "bot" and "Ban Members" permissions.

https://discordpy.readthedocs.io/en/stable/discord.html

### Matrix Bot

Set up a new user account on your homeserver of choice and invite it to rooms.  Give user account moderator privileges in all rooms.

### Install

    $ git clone https://github.com/mcarlton00/jf-ban-bot.git
    $ cd jf-ban-bot
    $ python -m venv venv
    $ venv/bin/pip install -r requirements.txt
    $ cp config.ini.template config.ini

Edit `config.ini` with the details of your accounts.

## Run

    $ venv/bin/python banbot.py
