#!/usr/bin/env python3
"""
Mumble Half-Duplex Communication Bot
Implements push-to-talk style communication in Mumble channels
"""
import pymumble_py3
import time
import logging
import signal
import sys
import threading
from datetime import datetime
import configparser


class HalfDuplexBot:
    def __init__(self, config):
        self.config = config
        self.mumble = None
        self.running = True
        self.current_speaker = None
        self.target_channel = None
        self.speak_timers = {}
        self.lock = threading.Lock()

        # Setup enhanced logging
        level = logging.DEBUG if config['debug'] else logging.INFO
        logging.basicConfig(
            level=level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger('HalfDuplexBot')

    def connect(self):
        """Connect to Mumble server"""
        self.logger.info(f"Connecting to {self.config['server']}:{self.config['port']}")

        self.mumble = pymumble_py3.Mumble(
            self.config['server'],
            self.config['username'],
            port=self.config['port'],
            password=self.config['password'],
            certfile=self.config['certfile']
        )

        # Set callbacks
        self.mumble.callbacks.set_callback('sound_received', self.on_sound_received)
        self.mumble.callbacks.set_callback('connected', self.on_connected)
        self.mumble.callbacks.set_callback('user_updated', self.on_user_updated)

        # Start connection
        self.mumble.start()
        self.mumble.is_ready()

    def on_connected(self):
        """Called when connected to server"""
        self.logger.info("Connected to Mumble server")

        # Debug: Log all available channels
        self.logger.debug("Available channels:")
        for channel_id, channel in self.mumble.channels.items():
            self.logger.debug(f"  {channel_id}: {channel['name']}")

        # Find and join target channel
        channels = self.mumble.channels
        for channel_id, channel in channels.items():
            if channel['name'] == self.config['channel']:
                self.target_channel = channel
                self.mumble.channels[channel_id].move_in()
                self.logger.info(f"Joined channel: {channel['name']}")
                break

        if not self.target_channel:
            self.logger.error(f"Channel '{self.config['channel']}' not found!")
            self.stop()

        # Debug: Log initial users in channel
        self._log_channel_users()

        # Check if bot has permissions
        myself = self.mumble.users.myself
        self.logger.info(f"Bot info: {myself}")

    def on_user_updated(self, user, actions):
        """DEBUG: Log user updates"""
        self.logger.debug(f"User updated: {user.get('name', 'Unknown')} - Actions: {actions}")

    def on_sound_received(self, user, soundchunk):
        """Handle incoming audio to detect speaking users"""
        # DEBUG: Log every sound event
        self.logger.debug(f"Sound received from {user.get('name', 'Unknown')} (ID: {user.get('session')})")

        if not self.target_channel or not self.running:
            self.logger.debug("No target channel or not running")
            return

        # Check if user is in our target channel
        if user['channel_id'] != self.target_channel['channel_id']:
            self.logger.debug(f"User {user.get('name')} not in target channel")
            return

        user_id = user['session']
        username = user['name']

        # Don't process audio from the bot itself
        if username == self.config['username']:
            return

        self.logger.info(f"VOICE ACTIVITY: {username} is speaking")

        # Cancel any pending restore timer for this user
        with self.lock:
            if user_id in self.speak_timers:
                self.speak_timers[user_id].cancel()
                del self.speak_timers[user_id]
                self.logger.debug(f"Cancelled restore timer for {username}")

        # If this is a new speaker or speaker changed
        if self.current_speaker != user_id:
            self.logger.info(f"NEW SPEAKER: {username} (was: {self.current_speaker})")

            # Set as current speaker
            previous_speaker = self.current_speaker
            self.current_speaker = user_id

            # Revoke speak permissions from others (with slight delay)
            self.logger.info(f"Scheduling mute of others in {self.config['speak_delay']}s")
            threading.Timer(self.config['speak_delay'],
                            self._revoke_others_speak,
                            args=(user_id,)).start()

        # Set timer to restore permissions when user stops speaking
        with self.lock:
            timer = threading.Timer(self.config['restore_delay'],
                                    self._restore_speak_permissions,
                                    args=(user_id,))
            self.speak_timers[user_id] = timer
            timer.start()
            self.logger.debug(f"Set restore timer for {username}")

    def _revoke_others_speak(self, speaker_id):
        """Revoke speak permission from all users except the speaker"""
        self.logger.info(f"EXECUTING MUTE: Speaker ID {speaker_id}")

        # Get users in channel
        users_in_channel = self._get_users_in_channel()

        # Check bot permissions
        myself = self.mumble.users.myself
        self.logger.info(f"Bot permissions check - myself: {myself}")

        for user in users_in_channel:
            if user['session'] != speaker_id and user['name'] != self.config['username']:
                try:
                    self.logger.info(f"Attempting to mute: {user['name']}")
                    # Try server-side mute
                    user_obj = self.mumble.users[user['session']]
                    user_obj.mute()
                    self.logger.info(f"Mute command sent for {user['name']}")
                except PermissionError as e:
                    self.logger.error(f"Permission denied: {e}")
                except Exception as e:
                    self.logger.error(f"Failed to mute {user['name']}: {type(e).__name__}: {e}")

    def _restore_speak_permissions(self, user_id):
        """Restore speak permissions when user stops speaking"""
        self.logger.info(f"EXECUTING UNMUTE: User ID {user_id}")

        with self.lock:
            if user_id in self.speak_timers:
                del self.speak_timers[user_id]

        # Only restore if this was the current speaker
        if self.current_speaker == user_id:
            self.logger.info(f"User {user_id} stopped speaking, restoring permissions")
            self.current_speaker = None

            # Restore permissions for all users
            users_in_channel = self._get_users_in_channel()

            for user in users_in_channel:
                if user['name'] != self.config['username']:
                    try:
                        self.logger.info(f"Attempting to unmute: {user['name']}")
                        user_obj = self.mumble.users[user['session']]
                        user_obj.unmute()
                        self.logger.info(f"Unmute command sent for {user['name']}")
                    except Exception as e:
                        self.logger.error(f"Failed to unmute {user['name']}: {e}")

    def _get_users_in_channel(self):
        """Get list of users in target channel"""
        users = []
        if self.target_channel:
            for session, user in self.mumble.users.items():
                if user['channel_id'] == self.target_channel['channel_id']:
                    users.append(user)
                    self.logger.debug(f"User in channel: {user['name']} (ID: {session})")
        return users

    def _log_channel_users(self):
        """Debug helper to log all users in channel"""
        users = self._get_users_in_channel()
        self.logger.info(f"=== CHANNEL USERS ({len(users)}) ===")
        for user in users:
            self.logger.info(f"  - {user['name']} (ID: {user['session']})")
        self.logger.info("=== END CHANNEL USERS ===")

    def run(self):
        """Main bot loop with periodic status"""
        self.logger.info("Bot is running. Press Ctrl+C to stop.")

        try:
            counter = 0
            while self.running:
                time.sleep(5)  # Check every 5 seconds
                counter += 1
                if counter % 12 == 0:  # Every minute
                    self.logger.info(f"Bot status: Running, current speaker: {self.current_speaker}")
                    self._log_channel_users()
        except KeyboardInterrupt:
            self.logger.info("Keyboard interrupt received")
            self.stop()

    def stop(self):
        """Stop the bot gracefully"""
        self.logger.info("Stopping bot...")
        self.running = False

        # Cancel all timers
        with self.lock:
            for timer in self.speak_timers.values():
                timer.cancel()
            self.speak_timers.clear()

        # Restore all permissions before leaving
        if self.mumble and self.mumble.connected:
            users = self._get_users_in_channel()
            for user in users:
                try:
                    user_obj = self.mumble.users[user['session']]
                    user_obj.unmute()
                except:
                    pass

            self.mumble.stop()

        self.logger.info("Bot stopped")


def load_config(config_file=None):
    """Load configuration from file"""
    CONFIG = {
        'server': 'localhost',
        'port': 64738,
        'username': 'HalfDuplexBot',
        'password': '',
        'channel': 'Half-Duplex Channel',
        'certfile': None,
        'speak_delay': 0.2,
        'restore_delay': 0.5,
        'debug': True
    }

    if config_file:
        config = configparser.ConfigParser()
        config.read(config_file)

        if 'bot' in config:
            CONFIG.update(dict(config['bot']))
            # Convert numeric values
            if 'port' in CONFIG:
                CONFIG['port'] = int(CONFIG['port'])
            if 'speak_delay' in CONFIG:
                CONFIG['speak_delay'] = float(CONFIG['speak_delay'])
            if 'restore_delay' in CONFIG:
                CONFIG['restore_delay'] = float(CONFIG['restore_delay'])
            if 'debug' in CONFIG:
                CONFIG['debug'] = CONFIG['debug'].lower() == 'true'

    return CONFIG


def main():
    """Main entry point"""
    bot = None

    def signal_handler(sig, frame):
        if bot:
            bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    config = load_config('halfduplex.conf')

    bot = HalfDuplexBot(config)

    try:
        bot.connect()
        bot.run()
    except Exception as e:
        logging.error(f"Bot error: {e}")
        if bot:
            bot.stop()


if __name__ == '__main__':
    main()