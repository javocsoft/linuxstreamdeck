"""The pre-flight report in full sentences.

A key carries two words. This carries what each result actually establishes and,
just as importantly, what it does not: several checks can only be answered on
the machine OBS runs on, and the camera one covers V4L2 devices only. Those
appear here as their own rows rather than being left out, because a check that
is quietly missing looks exactly like one that passed.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..core.preflight import FAIL, OK, UNCHECKED, WARN, Report  # noqa: E402

# Row icons, chosen so the four states are told apart without relying on colour.
STATE_ICONS = {
    OK: "emblem-ok-symbolic",
    WARN: "dialog-warning-symbolic",
    FAIL: "dialog-error-symbolic",
    UNCHECKED: "dialog-question-symbolic",
}
STATE_WORDS = {
    OK: "Checked",
    WARN: "Worth a look",
    FAIL: "Problem",
    UNCHECKED: "Not checked",
}
STATE_CSS = {
    WARN: "warning",
    FAIL: "error",
    UNCHECKED: "dim-label",
}


class PreFlightDialog(Adw.Window):
    def __init__(self, parent, checks) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Pre-flight check",
            default_width=560,
            default_height=620,
        )
        report = Report(checks=list(checks))

        page = Adw.PreferencesPage()
        page.add(self._results_group(report))
        page.add(self._scope_group())

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        view.add_top_bar(header)
        view.set_content(page)

        close = Gtk.Button(label="Close")
        close.add_css_class("suggested-action")
        close.connect("clicked", lambda _b: self.close())
        bottom = Gtk.Box(halign=Gtk.Align.END, margin_top=8, margin_bottom=8,
                         margin_start=12, margin_end=12)
        bottom.append(close)
        view.add_bottom_bar(bottom)
        self.set_content(view)

    def _results_group(self, report: Report) -> Adw.PreferencesGroup:
        counts = report.counts()
        group = Adw.PreferencesGroup(
            title=report.summary(),
            description=(
                # Said once, plainly, at the top. Everything below is a list of
                # answers, and this is the sentence that stops the list being
                # read as a guarantee.
                "This reports what could be established just now. It is not a "
                "promise that the stream will go well, and anything marked "
                "«Not checked» was not established either way."
                if counts[UNCHECKED]
                else "This reports what could be established just now. It is "
                     "not a promise that the stream will go well."
            ),
        )
        for check in report.checks:
            row = Adw.ActionRow(
                title=check.label,
                subtitle=check.detail,
                subtitle_lines=0,
            )
            icon = Gtk.Image.new_from_icon_name(
                STATE_ICONS.get(check.state, STATE_ICONS[UNCHECKED])
            )
            css = STATE_CSS.get(check.state)
            if css:
                icon.add_css_class(css)
            row.add_prefix(icon)
            state = Gtk.Label(label=STATE_WORDS.get(check.state, ""))
            state.add_css_class("dim-label")
            state.add_css_class("caption")
            row.add_suffix(state)
            group.add(row)
        return group

    @staticmethod
    def _scope_group() -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="What this cannot tell you",
            description=(
                "Worth knowing before trusting a row of ticks."
            ),
        )
        for title, subtitle in (
            (
                "Cameras: the device, not the picture",
                "It checks that OBS holds a capture device open for each V4L2 "
                "source. A camera with its privacy shutter closed, pointing at "
                "a wall or badly exposed still reads as fine. Browser, window "
                "and screen capture sources are not covered at all.",
            ),
            (
                "Audio: reaching OBS, not reaching your viewers",
                "It listens to OBS's own level meters for a couple of seconds. "
                "If you were not speaking, silence is expected. It says nothing "
                "about whether the audio is routed to your stream.",
            ),
            (
                "Disk, recording folder and cameras are local",
                "They describe the computer OBS runs on. Against a remote OBS "
                "they are reported as not checked rather than guessed at.",
            ),
            (
                "The stream key was not tested",
                "Only whether one is configured. Its value is never read, shown "
                "or logged, and whether the service accepts it is unknown until "
                "you go live.",
            ),
            (
                "Twitch: that a title and category exist, not that they are right",
                "Left on yesterday's game is the classic mistake and this "
                "cannot catch it — only that something is set. With no Twitch "
                "account connected the four Twitch rows are reported as not "
                "checked rather than left out.",
            ),
        ):
            row = Adw.ActionRow(title=title, subtitle=subtitle, subtitle_lines=0)
            row.add_css_class("dim-label")
            group.add(row)
        return group
