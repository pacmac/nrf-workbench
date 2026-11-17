"""The console: reading what a device says, and commanding it, through a channel that is not the device's own USB.

See docs/specs/console.md. This package is the ONLY one that opens a serial port to read and write it (a gate test
enforces it, with two named, older exceptions); it never opens a port that is not a declared link.
"""
