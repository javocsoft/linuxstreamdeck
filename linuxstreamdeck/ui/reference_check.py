"""Report keys that point at OBS objects which no longer exist.

Started by hand, never on its own. Switching scene collection in OBS replaces
every scene name at once, and a checker that ran by itself would report the
whole page as broken every time that happened — which teaches you to ignore it,
so the day something really breaks you ignore that too. Because the user starts
it, standing where they are, the result is always read in the right context.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

log = logging.getLogger(__name__)


class ReferenceCheckDialog(Adw.Window):
    def __init__(self, window, report) -> None:
        super().__init__(
            transient_for=window,
            modal=True,
            title="Check keys against OBS",
            default_width=560,
            default_height=520,
        )
        self.window = window
        self.app = window.app
        self.report = report

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self._page = Adw.PreferencesPage()
        self._build()
        view.set_content(self._page)
        self.set_content(view)

    # ---------- contents ----------

    def _build(self) -> None:
        report = self.report
        where = self.app.controller.folder_trail()
        place = " › ".join(name for _path, name in where) if where else (
            self.app.controller.page.name
        )
        group = Adw.PreferencesGroup(
            title=f"Checked «{place}» against collection «{report.collection}»"
            if report.collection
            else f"Checked «{place}» against OBS",
            description=(
                "Only what you are looking at was checked, against the scene "
                "collection OBS has loaded right now. Keys built for another "
                "collection would look missing here."
            ),
        )
        if report.is_clean():
            group.add(self._clean_row())
        else:
            for finding in report.findings:
                group.add(self._finding_row(finding))
        self._page.add(group)

        summary = Adw.PreferencesGroup()
        summary.add(self._summary_row())
        self._page.add(summary)

    def _clean_row(self) -> Adw.ActionRow:
        return Adw.ActionRow(
            title="Everything resolves",
            subtitle="No key points at something OBS no longer has.",
        )

    def _finding_row(self, finding) -> Adw.ActionRow:
        row = Adw.ActionRow(title=finding.summary())
        row.set_subtitle(self._where(finding))
        if finding.suggestion:
            button = Gtk.Button(
                label=f"Repoint to «{finding.suggestion}»",
                valign=Gtk.Align.CENTER,
            )
            button.add_css_class("suggested-action")
            button.connect("clicked", self._confirm, finding)
            row.add_suffix(button)
        return row

    @staticmethod
    def _where(finding) -> str:
        """Which keys hold it, named rather than counted."""
        places = []
        for reference in finding.references:
            if reference.where not in places:
                places.append(reference.where)
        shown = ", ".join(places[:4])
        return f"{shown}, …" if len(places) > 4 else shown

    def _summary_row(self) -> Adw.ActionRow:
        report = self.report
        # Saying how much was checked matters as much as what failed: a report
        # that only ever speaks up on problems never tells you it looked.
        if report.is_clean():
            subtitle = (
                f"{report.checked} reference(s) across "
                f"{report.keys} configured key(s)."
            )
        else:
            subtitle = (
                f"{report.checked} reference(s) checked; "
                f"{report.broken_keys()} key(s) need attention. Repointing "
                "saves a backup first, so it can be undone from "
                "Restore a backup."
            )
        row = Adw.ActionRow(title="Result", subtitle=subtitle)
        row.set_sensitive(False)
        return row

    # ---------- fixing ----------

    def _confirm(self, _button, finding) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Repoint these keys?",
            body=(
                f"{len(finding.references)} reference(s) to "
                f"«{finding.value}» will be changed to «{finding.suggestion}».\n\n"
                "The current configuration is saved as a backup first, so this "
                "can be undone from Restore a backup."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("fix", "Repoint")
        dialog.set_response_appearance("fix", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_response, finding)
        dialog.present()

    def _on_response(self, _dialog, response: str, finding) -> None:
        if response != "fix":
            return
        try:
            fixed = self.app.controller.apply_reference_fix(
                finding, finding.suggestion
            )
        except Exception as error:
            log.exception("Could not repoint the references")
            self.window._show_configuration_error("Repoint failed", str(error))
            return
        self.app.bus.emit(
            "status",
            text=f"Repointed {fixed} reference(s) to {finding.suggestion}",
        )
        # The report describes a configuration that has just changed, so it is
        # taken again rather than left showing what was fixed a moment ago.
        self._recheck()

    def _recheck(self) -> None:
        try:
            self.report = self.app.controller.check_references()
        except Exception:
            self.close()
            return
        while (child := self._page.get_first_child()) is not None:
            self._page.remove(child)
        self._build()
