#!/usr/bin/env python3
import pymumble.pymumble_py3 as pymumble
import time
import threading


class HalfDuplexBot:
    def __init__(self, server, port, username, password=None):
        self.mumble = pymumble.Mumble(server, username, port=port, password=password)
        self.current_speaker = None
        self.speaker_lock = threading.Lock()

    def start(self):
        # Connect to server
        self.mumble.start()
        self.mumble.is_ready()

        # Register callbacks
        self.mumble.users.myself.unmute()
        self.mumble.users.myself.undeafen()

        # Set up voice state monitoring
        self.mumble.callbacks.set_callback("on_user_talk_state_changed", self.on_talk_state_changed)

        print(f"Bot connected as {self.mumble.users.myself.get_property('name')}")

    def on_talk_state_changed(self, user):
        """Handle user talking state changes"""
        with self.speaker_lock:
            if user.get_property('talking'):
                # User started talking
                if self.current_speaker is None:
                    self.current_speaker = user
                    self.mute_all_except(user)
                    print(f"{user.get_property('name')} is now speaking")
            else:
                # User stopped talking
                if self.current_speaker == user:
                    self.current_speaker = None
                    self.unmute_all()
                    print(f"{user.get_property('name')} stopped speaking")

    def mute_all_except(self, speaker):
        """Server-mute all users except the speaker"""
        for user_id, user in self.mumble.users.items():
            if user != speaker and user != self.mumble.users.myself:
                try:
                    user.mute()
                except Exception as e:
                    print(f"Failed to mute {user.get_property('name')}: {e}")

    def unmute_all(self):
        """Unmute all users"""
        for user_id, user in self.mumble.users.items():
            if user != self.mumble.users.myself:
                try:
                    user.unmute()
                except Exception as e:
                    print(f"Failed to unmute {user.get_property('name')}: {e}")

    def run(self):
        """Keep the bot running"""
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Shutting down...")
            self.mumble.stop()


if __name__ == "__main__":
    # Configuration
    SERVER = "your.mumble.server"
    PORT = 64738  # Default Mumble port
    USERNAME = "HalfDuplexBot"
    PASSWORD = "your_server_password"  # Optional

    # Create and run bot
    bot = HalfDuplexBot(SERVER, PORT, USERNAME, PASSWORD)
    bot.start()
    bot.run()