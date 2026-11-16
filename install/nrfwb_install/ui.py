"""Talking to the person, and refusing to block when there is nobody to talk to.

Standard input is the TERMINAL when there is one (install.sh gives it explicitly), or nothing. It is
never the pipe a `curl | sh` came from. With no terminal a question is never asked: it either takes
a SAFE default under --yes, or raises NeedsPerson so the caller prints the plan and stops.
"""
import sys


class NeedsPerson(Exception):
    """A decision only a person can make, and there is no terminal (or --yes does not cover it)."""

    def __init__(self, what):
        Exception.__init__(self, what)
        self.what = what


class Ui(object):
    def __init__(self, yes=False):
        self.yes = yes
        self.terminal = sys.stdin.isatty()

    def say(self, text=""):
        print(text, flush=True)

    def warn(self, text):
        print(text, file=sys.stderr, flush=True)

    def ask(self, question):
        """Ask on the terminal and return the reply. No terminal: NeedsPerson, never a block."""
        if not self.terminal:
            raise NeedsPerson(question)
        try:
            return input(question + " ").strip()
        except EOFError:
            raise NeedsPerson(question)

    def confirm(self, question, safe=False):
        """True or False. `safe` marks a question whose YES is a safe default that --yes may take.
        Something that changes privilege or the system (a group, a system package) is never safe."""
        if self.terminal:
            return self.ask(question + " [y/N]").lower() in ("y", "yes")
        if self.yes and safe:
            return True
        raise NeedsPerson(question)
