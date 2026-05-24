"""Minimal Studio TUI for StoryForge2."""
from __future__ import annotations

from pathlib import Path

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container
    from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static
    from textual.binding import Binding
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False
    # Create module-level dummy for when textual is not installed
    App = object
    Container = object
    ListItem = object
    ListView = object
    Label = object
    Header = object
    Footer = object
    Binding = None

from engine.storage import JsonStateStore
from engine.schemas.chapter import ChapterStage


STAGE_COLORS = {
    ChapterStage.PLANNED: "blue",
    ChapterStage.COMPOSED: "cyan",
    ChapterStage.DRAFTED: "yellow",
    ChapterStage.SETTLED: "yellow",
    ChapterStage.AUDITED_PASSED: "green",
    ChapterStage.AUDITED_FAILED: "red",
    ChapterStage.REVISING: "orange",
    ChapterStage.ROLLED_BACK: "red",
    ChapterStage.APPROVED: "green",
    ChapterStage.EXPORTED: "bold green",
    ChapterStage.HUMAN_REVIEW_REQUIRED: "bold red",
    ChapterStage.INVALIDATED: "dim red",
    ChapterStage.BLOCKED: "dim",
}


def stage_display(stage: ChapterStage) -> str:
    color = STAGE_COLORS.get(stage, "white")
    return f"[{color}]{stage.value}[/{color}]"


class ChapterListItem(ListItem):
    def __init__(self, chapter_no: int, stage: ChapterStage, revision_round: int) -> None:
        super().__init__()
        self.chapter_no = chapter_no
        self.stage = stage
        self.revision_round = revision_round


class BookView(Container):
    def __init__(self, store: JsonStateStore, book_id: str) -> None:
        super().__init__()
        self.store = store
        self.book_id = book_id

    def compose(self) -> ComposeResult:
        yield Label(f"Book: {self.book_id}", classes="title")
        chapters = self.store.list_chapters(self.book_id)
        items = []
        for ch in chapters:
            stage = ChapterStage(ch.stage)
            item = ChapterListItem(ch.chapter_no, stage, ch.revision_round)
            item.add(Label(f"Ch {ch.chapter_no:04d}: {stage_display(stage)} (rev={ch.revision_round})"))
            items.append(item)
        yield ListView(*items, id="chapter-list")


class StudioApp(App):
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    CSS = """
    .title {
        text-style: bold;
        padding: 1;
    }
    ListView {
        height: 100%;
    }
    ListItem {
        padding: 1;
    }
    """

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.store = JsonStateStore(root)
        self.book_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        books = self.store.list_books()
        if books:
            self.book_id = books[0].book_id
            yield BookView(self.store, self.book_id)
        else:
            yield Label("No books found")
        yield Footer()

    def action_refresh(self) -> None:
        self.refresh()


def run_studio(root: Path) -> None:
    if not TEXTUAL_AVAILABLE:
        print("Error: textual not installed. Run: pip install textual")
        return
    app = StudioApp(root)
    app.run()


if __name__ == "__main__":
    import sys
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    run_studio(root)