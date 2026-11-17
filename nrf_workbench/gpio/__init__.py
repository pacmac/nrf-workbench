"""The GPIO area (docs/specs/gpio.md). This slice is the pin MAP only: which header pin is assigned to which service
and action. Nothing here opens a gpiochip, requests a line, or reads or drives a pin; a gate test proves it."""
