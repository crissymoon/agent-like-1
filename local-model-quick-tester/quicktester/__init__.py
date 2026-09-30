"""A quick tester for the models on this disk, in the models' own terms.

The pieces are separated by what each one has to know and nothing else:

    theme       the palette, read from the project's own stylesheet
    textui      boxes, menus, prompts, the spinner and the progress bar
    paths       where the repository is, and how a path may be written down
    settings    what describes this machine, kept out of the repository
    platforms   which machine this is, and what it takes to build a runtime
    installer   running a build plan, or printing one for another machine
    registry    models.toml: which weight file is which kind of model
    modes       modes.toml: the system prompt of each way of conversing
    transcript  the markdown copy written after a conversation
    text        a conversation, through llama.cpp
    image       a generation, through the shared diffusion package
    inspect     reading a weight file without running it
    actions     every capability, once, for both the menu and the command line
    screens     settings, transcripts, help, and the comparison
    menu        the screen with no arguments

Two rules hold the whole thing together. The registry decides which file is which
kind of model, so no module names a model. And nothing is described twice: the
image profiles live with the diffusion loader, the byte formatting lives with the
plan, and this package reads them rather than repeating them.
"""

from __future__ import annotations

__all__ = [
    "actions",
    "image",
    "inspect",
    "installer",
    "menu",
    "modes",
    "paths",
    "platforms",
    "registry",
    "screens",
    "settings",
    "text",
    "textui",
    "theme",
    "transcript",
]

__version__ = "1.0.0"
