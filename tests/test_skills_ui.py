from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTabWidget

from flowai.models import Workflow
from flowai.skills import DEFAULT_CATEGORY, scan_skills
from flowai.ui.main_window import WorkflowSettingsDialog
from flowai.ui.skills_page import SkillsPage


@pytest.fixture(autouse=True)
def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_skill(root: Path, name: str, description: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return directory


def test_page_groups_skills_into_sections(tmp_path: Path) -> None:
    make_skill(tmp_path, "image-cutout", "Прибирає фон")
    make_skill(tmp_path, "develop-web-game", "Робить гру")
    page = SkillsPage(root=tmp_path, categories_path=tmp_path / "cat.json")
    page.set_category("image-cutout", "Images")
    page.refresh()
    headings = [
        page.tree.topLevelItem(index).text(0)
        for index in range(page.tree.topLevelItemCount())
    ]
    assert headings == ["Images", DEFAULT_CATEGORY]


def test_page_shows_the_skill_text_when_selected(tmp_path: Path) -> None:
    make_skill(tmp_path, "birds-map", "Розкладає карту")
    page = SkillsPage(root=tmp_path, categories_path=tmp_path / "cat.json")
    page.refresh()
    section = page.tree.topLevelItem(0)
    page.tree.setCurrentItem(section.child(0))
    assert "birds-map" in page.viewer.toPlainText()
    assert page.current_entry() is not None
    assert page.current_entry().name == "birds-map"


def test_page_refuses_to_delete_a_system_skill(tmp_path: Path) -> None:
    make_skill(tmp_path / ".system", "imagegen", "Картинки")
    page = SkillsPage(root=tmp_path, categories_path=tmp_path / "cat.json")
    page.refresh()
    section = page.tree.topLevelItem(0)
    page.tree.setCurrentItem(section.child(0))
    assert page.delete_button.isEnabled() is False


def test_page_enables_delete_for_a_user_skill(tmp_path: Path) -> None:
    make_skill(tmp_path, "birds-map", "Карта")
    page = SkillsPage(root=tmp_path, categories_path=tmp_path / "cat.json")
    page.refresh()
    section = page.tree.topLevelItem(0)
    page.tree.setCurrentItem(section.child(0))
    assert page.delete_button.isEnabled() is True


def test_category_is_persisted(tmp_path: Path) -> None:
    make_skill(tmp_path, "image-cutout", "Фон")
    target = tmp_path / "cat.json"
    page = SkillsPage(root=tmp_path, categories_path=target)
    page.set_category("image-cutout", "Images")
    assert "Images" in target.read_text(encoding="utf-8")


def test_settings_dialog_has_two_tabs() -> None:
    dialog = WorkflowSettingsDialog(Workflow(name="Тест"))
    assert isinstance(dialog.tabs, QTabWidget)
    assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == [
        "Flow",
        "Skills",
    ]


def test_settings_dialog_still_saves_the_workflow_name() -> None:
    dialog = WorkflowSettingsDialog(Workflow(name="Тест"))
    dialog.name_edit.setText("Нова назва")
    assert dialog.name_edit.text() == "Нова назва"
    assert dialog.additional_folders() == []


def test_broken_skill_is_shown_with_its_error(tmp_path: Path) -> None:
    (tmp_path / "broken").mkdir(parents=True)
    page = SkillsPage(root=tmp_path, categories_path=tmp_path / "cat.json")
    page.refresh()
    section = page.tree.topLevelItem(0)
    child = section.child(0)
    assert "SKILL.md" in child.toolTip(0)
    assert scan_skills(tmp_path)[0].error
