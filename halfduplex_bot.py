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

# Configuration
CONFIG = {
    'server': 'localhost',
    'port': 64738,
    'username': 'HalfDuplexBot',
    'password': '',
    'channel': 'Half-Duplex Channel',  # Target channel name
    'certfile': None,  # Optional client certificate
    'speak_delay': 0.2,  # Delay before revoking permissions (seconds)
    'restore_delay': 0.5,  # Delay before restoring permissions (seconds)
    'debug': True
}

class HalfDuplexBot:
    def __init__(self, config):
        self.config = config
        self.mumble = None
        self.running = True
        self.current_speaker = None
        self.target_channel = None
        self.speak_timers = {}
        self.lock = threading.Lock()
        
        # Setup logging
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
        
        # Start connection
        self.mumble.start()
        self.mumble.is_ready()  # Wait for connection
        
    def on_connected(self):
        """Called when connected to server"""
        self.logger.info("Connected to Mumble server")
        
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
            
    def on_sound_received(self, user, soundchunk):
        """Handle incoming audio to detect speaking users"""
        if not self.target_channel or not self.running:
            return
            
        # Check if user is in our target channel
        if user['channel_id'] != self.target_channel['channel_id']:
            return
            
        user_id = user['session']
        username = user['name']
        
        # Cancel any pending restore timer for this user
        with self.lock:
            if user_id in self.speak_timers:
                self.speak_timers[user_id].cancel()
                del self.speak_timers[user_id]
        
        # If this is a new speaker or speaker changed
        if self.current_speaker != user_id:
            self.logger.debug(f"{username} started speaking")
            
            # Set as current speaker
            previous_speaker = self.current_speaker
            self.current_speaker = user_id
            
            # Revoke speak permissions from others (with slight delay)
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
            
    def _revoke_others_speak(self, speaker_id):
        """Revoke speak permission from all users except the speaker"""
        if not self.running or self.current_speaker != speaker_id:
            return
            
        users_in_channel = self._get_users_in_channel()
        
        for user in users_in_channel:
            if user['session'] != speaker_id:
                try:
                    # Mute user (remove speak permission)
                    self.mumble.users[user['session']].mute()
                    self.logger.debug(f"Muted {user['name']}")
                except Exception as e:
                    self.logger.error(f"Failed to mute {user['name']}: {e}")
                    
    def _restore_speak_permissions(self, user_id):
        """Restore speak permissions when user stops speaking"""
        with self.lock:
            if user_id in self.speak_timers:
                del self.speak_timers[user_id]
                
        # Only restore if this was the current speaker
        if self.current_speaker == user_id:
            self.logger.debug(f"User {user_id} stopped speaking")
            self.current_speaker = None
            
            # Restore permissions for all users
            users_in_channel = self._get_users_in_channel()
            
            for user in users_in_channel:
                try:
                    # Unmute user
                    self.mumble.users[user['session']].unmute()
                    self.logger.debug(f"Unmuted {user['name']}")
                except Exception as e:
                    self.logger.error(f"Failed to unmute {user['name']}: {e}")
                    
    def _get_users_in_channel(self):
        """Get list of users in target channel"""
        users = []
        if self.target_channel:
            for session, user in self.mumble.users.items():
                if user['channel_id'] == self.target_channel['channel_id']:
                    users.append(user)
        return users
        
    def run(self):
        """Main bot loop"""
        self.logger.info("Bot is running. Press Ctrl+C to stop.")
        
        try:
            while self.running:
                time.sleep(1)
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
                    self.mumble.users[user['session']].unmute()
                except:
                    pass
                    
            self.mumble.stop()
            
        self.logger.info("Bot stopped")
        

# For multiple channel support
class MultiChannelHalfDuplexBot(HalfDuplexBot):
    """Extended bot that supports multiple channels"""
    
    def __init__(self, config):
        super().__init__(config)
        # Store speaker state per channel
        self.channel_speakers = {}  # channel_id: current_speaker_id
        self.channel_timers = {}    # channel_id: {user_id: timer}
        
    def on_sound_received(self, user, soundchunk):
        """Handle incoming audio for multiple channels"""
        if not self.running:
            return
            
        channel_id = user['channel_id']
        user_id = user['session']
        username = user['name']
        
        # Check if this channel is configured for half-duplex
        channel = self.mumble.channels.get(channel_id)
        if not channel or not self._is_halfduplex_channel(channel['name']):
            return
            
        # Initialize channel data if needed
        if channel_id not in self.channel_speakers:
            self.channel_speakers[channel_id] = None
            self.channel_timers[channel_id] = {}
            
        # Cancel pending restore timer for this user in this channel
        with self.lock:
            if user_id in self.channel_timers[channel_id]:
                self.channel_timers[channel_id][user_id].cancel()
                del self.channel_timers[channel_id][user_id]
                
        # Handle speaker change
        if self.channel_speakers[channel_id] != user_id:
            self.logger.debug(f"{username} started speaking in {channel['name']}")
            self.channel_speakers[channel_id] = user_id
            
            threading.Timer(self.config['speak_delay'],
                          self._revoke_others_speak_multichannel,
                          args=(channel_id, user_id)).start()
                          
        # Set restore timer
        with self.lock:
            timer = threading.Timer(self.config['restore_delay'],
                                  self._restore_speak_multichannel,
                                  args=(channel_id, user_id))
            self.channel_timers[channel_id][user_id] = timer
            timer.start()
            
    def _is_halfduplex_channel(self, channel_name):
        """Check if channel is configured for half-duplex"""
        # Can be extended to check against a list of channels
        return channel_name in self.config.get('channels', [self.config['channel']])
        
    def _revoke_others_speak_multichannel(self, channel_id, speaker_id):
        """Revoke speak permissions in specific channel"""
        if not self.running or self.channel_speakers.get(channel_id) != speaker_id:
            return
            
        for session, user in self.mumble.users.items():
            if user['channel_id'] == channel_id and user['session'] != speaker_id:
                try:
                    self.mumble.users[user['session']].mute()
                except Exception as e:
                    self.logger.error(f"Failed to mute {user['name']}: {e}")
                    
    def _restore_speak_multichannel(self, channel_id, user_id):
        """Restore speak permissions in specific channel"""
        with self.lock:
            if channel_id in self.channel_timers and user_id in self.channel_timers[channel_id]:
                del self.channel_timers[channel_id][user_id]
                
        if self.channel_speakers.get(channel_id) == user_id:
            self.logger.debug(f"Restoring permissions in channel {channel_id}")
            self.channel_speakers[channel_id] = None
            
            for session, user in self.mumble.users.items():
                if user['channel_id'] == channel_id:
                    try:
                        self.mumble.users[user['session']].unmute()
                    except Exception as e:
                        self.logger.error(f"Failed to unmute {user['name']}: {e}")


def load_config(config_file=None):
    """Load configuration from file"""
    if config_file:
        config = configparser.ConfigParser()
        config.read(config_file)
        
        if 'bot' in config:
            CONFIG.update(dict(config['bot']))
            
    return CONFIG


def main():
    """Main entry point"""
    # Setup signal handler
    def signal_handler(sig, frame):
        if bot:
            bot.stop()
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Load configuration
    config = load_config('halfduplex.conf')
    
    # Create and run bot
    global bot
    bot = HalfDuplexBot(config)
    # Use MultiChannelHalfDuplexBot for multiple channels:
    # bot = MultiChannelHalfDuplexBot(config)
    
    try:
        bot.connect()
        bot.run()
    except Exception as e:
        logging.error(f"Bot error: {e}")
        bot.stop()
        

if __name__ == '__main__':
    main()
