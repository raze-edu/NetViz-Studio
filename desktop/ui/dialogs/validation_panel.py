"""Dockable live 9-rule ValidationPanelWidget with severity filter and 1-click jump."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop.state import ProjectState
from shared.validator import ValidationIssue, validate_project

_SEVERITY_COLORS: dict[str, str] = {
    "error": "#EF4444",
    "warning": "#F59E0B",
    "info": "#38BDF8",
}


class ValidationPanelWidget(QWidget):
    """Dockable live 9-rule network and floor-plan validation panel."""

    issue_activated = Signal(object)  # ValidationIssue

    def __init__(self, state: ProjectState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._issues: list[ValidationIssue] = []
        self._build_ui()
        self._wire_signals()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        header = QHBoxLayout()
        self.summary_label = QLabel("0 issues", self)
        self.summary_label.setStyleSheet("font-weight: 600;")

        self.severity_combo = QComboBox(self)
        self.severity_combo.addItem("All Severities", userData="all")
        self.severity_combo.addItem("Errors Only", userData="error")
        self.severity_combo.addItem("Warnings Only", userData="warning")
        self.severity_combo.currentIndexChanged.connect(self._apply_filter)

        self.refresh_btn = QPushButton("Re-check", self)
        self.refresh_btn.clicked.connect(self.refresh)

        header.addWidget(self.summary_label, 1)
        header.addWidget(self.severity_combo)
        header.addWidget(self.refresh_btn)
        root.addLayout(header)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Severity", "Rule", "Details"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 88)
        self.tree.setColumnWidth(1, 185)
        self.tree.itemClicked.connect(self._on_item_activated)
        self.tree.itemDoubleClicked.connect(self._on_item_activated)
        root.addWidget(self.tree, 1)

    def _wire_signals(self) -> None:
        self._state.validation_updated.connect(self.set_issues)
        self._state.project_reloaded.connect(self.refresh)

    def current_issues(self) -> list[ValidationIssue]:
        """Return the latest full list of validation issues."""
        return list(self._issues)

    def refresh(self) -> list[ValidationIssue]:
        """Re-run 9-rule project validation and update the panel."""
        issues = validate_project(self._state.project)
        self.set_issues(issues)
        return issues

    def set_issues(self, issues: list[ValidationIssue]) -> None:
        """Replace displayed issues and update summary counts."""
        self._issues = list(issues)
        err_count = sum(1 for i in self._issues if i.severity == "error")
        warn_count = sum(1 for i in self._issues if i.severity == "warning")
        if not self._issues:
            self.summary_label.setText("0 issues (Project Healthy)")
            self.summary_label.setStyleSheet("color: #22C55E; font-weight: 600;")
        else:
            self.summary_label.setText(
                f"{len(self._issues)} issue(s) — {err_count} error(s), {warn_count} warning(s)"
            )
            color = "#EF4444" if err_count > 0 else "#F59E0B"
            self.summary_label.setStyleSheet(f"color: {color}; font-weight: 600;")
        self._apply_filter()

    def _apply_filter(self) -> None:
        sev_filter = str(self.severity_combo.currentData() or "all")
        self.tree.clear()
        for issue in self._issues:
            if sev_filter != "all" and issue.severity != sev_filter:
                continue
            item = QTreeWidgetItem([issue.severity.upper(), issue.rule_code, issue.message])
            item.setData(0, Qt.ItemDataRole.UserRole, issue)
            col = QColor(_SEVERITY_COLORS.get(issue.severity, "#CBD5E1"))
            item.setForeground(0, col)
            self.tree.addTopLevelItem(item)

    def _on_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        issue = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(issue, ValidationIssue):
            self.issue_activated.emit(issue)
