"""Kanasa for Enigma2."""
import logging

__version__ = "0.8.1"

# Enigma2 logs at DEBUG: keep urllib3 from writing request URLs (stream links, later) into the box logs
logging.getLogger("urllib3").setLevel(logging.WARNING)
