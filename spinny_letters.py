from __future__ import annotations

import json
import os
import random
import re
import string
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog


APP_TITLE = "Spinny Letters"
STARTING_TIME = 60
TIME_BONUS = 20
POINTS_PER_WORD = 100
HIGH_SCORE_LIMIT = 10
REEL_LENGTH = 17
CENTER_INDEX = REEL_LENGTH // 2
HIGH_SCORE_FILE = Path(__file__).with_name("spinny_letters_high_scores.json")
WORD_OVERRIDE_FILE = Path(__file__).with_name("spinny_letters_wordlist.txt")


class WordValidator:
    """Validate words with an American-English dictionary plus editable overrides."""

    FILE_HEADER = """# Spinny Letters dictionary overrides
#
# Add one instruction per line:
#   +word   Always allow a word
#   -word   Always reject a word
#
# Examples:
#   +element
#   -inum
#
# Lines without + or - are treated as allowed words.
"""

    def __init__(self) -> None:
        self.words: set[str] | None = None
        self.allowed_words: set[str] = set()
        self.blocked_words: set[str] = set()
        self.ensure_override_file()
        self.reload_overrides()

        try:
            import cmudict  # type: ignore

            # CMUdict is an American-English pronunciation dictionary.
            self.words = {
                re.sub(r"\(\d+\)$", "", word.lower())
                for word in cmudict.words()
                if word.isalpha()
            }
        except Exception:
            self.words = None

        self.pair_index: dict[tuple[str, str], list[str]] = {}
        self.rebuild_pair_index()

    def ensure_override_file(self) -> None:
        if not WORD_OVERRIDE_FILE.exists():
            WORD_OVERRIDE_FILE.write_text(self.FILE_HEADER, encoding="utf-8")

    def reload_overrides(self) -> None:
        self.allowed_words.clear()
        self.blocked_words.clear()
        self.ensure_override_file()

        try:
            lines = WORD_OVERRIDE_FILE.read_text(encoding="utf-8").splitlines()
        except OSError:
            return

        for raw_line in lines:
            line = raw_line.strip().lower()
            if not line or line.startswith("#"):
                continue

            action = "+"
            if line[0] in "+-":
                action, line = line[0], line[1:].strip()

            if not line.isalpha():
                continue

            if action == "-":
                self.blocked_words.add(line)
                self.allowed_words.discard(line)
            else:
                self.allowed_words.add(line)
                self.blocked_words.discard(line)

    def rebuild_pair_index(self) -> None:
        """Build a start/end-letter index from the active dictionary and overrides."""
        self.reload_overrides()
        source_words = set(self.words or set())
        source_words.update(self.allowed_words)
        source_words.difference_update(self.blocked_words)

        pair_index: dict[tuple[str, str], list[str]] = {}
        for word in source_words:
            if len(word) < 2 or not word.isalpha():
                continue
            pair_index.setdefault((word[0], word[-1]), []).append(word)

        for words in pair_index.values():
            words.sort()
        self.pair_index = pair_index

    def choose_valid_pair(self, minimum_words: int) -> tuple[str, str, int, str]:
        """Choose a playable pair, relaxing the threshold only if necessary."""
        self.rebuild_pair_index()
        if not self.pair_index:
            raise RuntimeError(
                "No playable words are available. Install cmudict or add words to the custom list."
            )

        eligible = [
            (pair, words)
            for pair, words in self.pair_index.items()
            if len(words) >= minimum_words
        ]
        if not eligible:
            eligible = list(self.pair_index.items())

        pair, words = random.choice(eligible)
        secret_word = random.choice(words)
        return pair[0].upper(), pair[1].upper(), len(words), secret_word

    def words_for_pair(self, first: str, last: str) -> list[str]:
        self.rebuild_pair_index()
        return list(self.pair_index.get((first.lower(), last.lower()), []))

    @property
    def dictionary_enabled(self) -> bool:
        return self.words is not None

    @property
    def mode_label(self) -> str:
        if self.dictionary_enabled:
            return "American English dictionary: ON"
        return "Dictionary missing: custom list only"

    def is_word(self, word: str) -> bool:
        normalized = word.lower()
        self.reload_overrides()

        if normalized in self.blocked_words:
            return False
        if normalized in self.allowed_words:
            return True
        if self.words is None:
            return False
        return normalized in self.words


class SpinnyLettersGame(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x680")
        self.minsize(980, 600)
        self.configure(bg="#111722")

        self.validator = WordValidator()
        self.high_scores = self.load_high_scores()

        self.game_active = False
        self.reels_spinning = False
        self.reels_stopping = False
        self.time_left = STARTING_TIME
        self.score = 0
        self.used_words: set[str] = set()
        self.round_words: list[str] = []
        self.selected_letters = ["?", "?"]
        self.target_letters = ["?", "?"]
        self.current_pair_word_count = 0
        self.current_secret_word = ""
        self.reel_letters = [self.make_reel(), self.make_reel()]
        self.reel_offsets = [0.0, 0.0]
        self.reel_speeds = [0.0, 0.0]
        self.last_tick = time.monotonic()
        self.timer_after_id: str | None = None
        self.animation_after_id: str | None = None
        self.pending_stops = 0

        self.build_ui()
        self.draw_reels()
        self.update_score_display()
        self.update_timer_display()
        self.refresh_high_scores()

        self.bind("<Return>", self.handle_enter_key)
        self.bind("<space>", self.handle_space_key)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI ----------

    def build_ui(self) -> None:
        header = tk.Frame(self, bg="#111722")
        header.pack(fill="x", padx=24, pady=(18, 8))

        title = tk.Label(
            header,
            text=APP_TITLE,
            font=("Segoe UI", 24, "bold"),
            fg="#f3f6fb",
            bg="#111722",
        )
        title.pack(side="left")

        self.timer_label = tk.Label(
            header,
            text="01:00",
            font=("Consolas", 30, "bold"),
            fg="#78f0b2",
            bg="#111722",
            width=7,
        )
        self.timer_label.place(relx=0.5, rely=0.5, anchor="center")

        self.start_button = tk.Button(
            header,
            text="START GAME",
            command=self.start_game,
            font=("Segoe UI", 12, "bold"),
            bg="#4b72ff",
            fg="white",
            activebackground="#6688ff",
            activeforeground="white",
            bd=0,
            padx=18,
            pady=10,
            cursor="hand2",
        )
        self.start_button.pack(side="right")

        self.word_list_button = tk.Button(
            header,
            text="EDIT WORD LIST",
            command=self.open_word_list,
            font=("Segoe UI", 11, "bold"),
            bg="#263449",
            fg="white",
            activebackground="#344760",
            activeforeground="white",
            bd=0,
            padx=16,
            pady=10,
            cursor="hand2",
        )
        self.word_list_button.pack(side="right", padx=(0, 10))

        content = tk.Frame(self, bg="#111722")
        content.pack(fill="both", expand=True, padx=24, pady=(8, 24))
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        # Score panel
        score_panel = self.make_panel(content, width=170)
        score_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        score_panel.grid_propagate(False)

        tk.Label(
            score_panel,
            text="SCORE",
            font=("Segoe UI", 13, "bold"),
            fg="#aab5c5",
            bg="#192231",
        ).pack(pady=(24, 8))

        self.score_label = tk.Label(
            score_panel,
            text="0",
            font=("Consolas", 34, "bold"),
            fg="#ffd66b",
            bg="#192231",
        )
        self.score_label.pack()

        tk.Label(
            score_panel,
            text=f"+{POINTS_PER_WORD} per word\n+{TIME_BONUS} seconds",
            justify="center",
            font=("Segoe UI", 10),
            fg="#7f8da0",
            bg="#192231",
        ).pack(pady=(8, 20))

        self.status_label = tk.Label(
            score_panel,
            text="Press Start",
            wraplength=140,
            justify="center",
            font=("Segoe UI", 11, "bold"),
            fg="#78f0b2",
            bg="#192231",
        )
        self.status_label.pack(padx=12, pady=16)

        validation_text = self.validator.mode_label
        tk.Label(
            score_panel,
            text=validation_text,
            wraplength=140,
            justify="center",
            font=("Segoe UI", 9),
            fg="#68788d",
            bg="#192231",
        ).pack(side="bottom", padx=10, pady=18)

        # Main game panel
        game_panel = self.make_panel(content)
        game_panel.grid(row=0, column=1, sticky="nsew", padx=0)

        tk.Label(
            game_panel,
            text="Stop the reels, then enter a word",
            font=("Segoe UI", 15, "bold"),
            fg="#f3f6fb",
            bg="#192231",
        ).pack(pady=(24, 12))

        self.reel_canvas = tk.Canvas(
            game_panel,
            height=260,
            bg="#0d131d",
            highlightthickness=0,
        )
        self.reel_canvas.pack(fill="x", padx=24, pady=(0, 14))
        self.reel_canvas.bind("<Configure>", lambda _event: self.draw_reels())

        self.spin_button = tk.Button(
            game_panel,
            text="STOP LETTERS",
            command=self.stop_reels,
            state="disabled",
            font=("Segoe UI", 13, "bold"),
            bg="#eb5b72",
            fg="white",
            activebackground="#ff7186",
            activeforeground="white",
            disabledforeground="#7b8491",
            bd=0,
            padx=24,
            pady=10,
            cursor="hand2",
        )
        self.spin_button.pack(pady=(0, 18))

        answer_row = tk.Frame(game_panel, bg="#192231")
        answer_row.pack(fill="x", padx=34, pady=(2, 10))

        self.prompt_label = tk.Label(
            answer_row,
            text="Word: ? ... ?",
            font=("Segoe UI", 13, "bold"),
            fg="#d9e0ea",
            bg="#192231",
        )
        self.prompt_label.pack(anchor="w", pady=(0, 7))

        entry_row = tk.Frame(answer_row, bg="#192231")
        entry_row.pack(fill="x")

        self.word_var = tk.StringVar()
        self.word_entry = tk.Entry(
            entry_row,
            textvariable=self.word_var,
            state="disabled",
            font=("Segoe UI", 17),
            bg="#0f1621",
            fg="#f3f6fb",
            insertbackground="white",
            disabledbackground="#121923",
            disabledforeground="#5e6876",
            relief="flat",
        )
        self.word_entry.pack(side="left", fill="x", expand=True, ipady=9)

        self.submit_button = tk.Button(
            entry_row,
            text="SUBMIT",
            command=self.submit_word,
            state="disabled",
            font=("Segoe UI", 11, "bold"),
            bg="#30b978",
            fg="white",
            activebackground="#42cf8a",
            activeforeground="white",
            disabledforeground="#77808d",
            bd=0,
            padx=20,
            pady=11,
            cursor="hand2",
        )
        self.submit_button.pack(side="left", padx=(10, 0))

        self.feedback_label = tk.Label(
            game_panel,
            text="",
            font=("Segoe UI", 11),
            fg="#ff8a9a",
            bg="#192231",
        )
        self.feedback_label.pack(pady=(0, 16))

        # Accepted words panel
        words_panel = self.make_panel(content, width=205)
        words_panel.grid(row=0, column=2, sticky="nsew", padx=(14, 14))
        words_panel.grid_propagate(False)

        tk.Label(
            words_panel,
            text="WORDS",
            font=("Segoe UI", 13, "bold"),
            fg="#aab5c5",
            bg="#192231",
        ).pack(pady=(20, 8))

        self.words_list = tk.Listbox(
            words_panel,
            font=("Segoe UI", 12),
            bg="#101722",
            fg="#e6ebf2",
            selectbackground="#31415a",
            relief="flat",
            highlightthickness=0,
            activestyle="none",
        )
        self.words_list.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        # High scores panel
        high_panel = self.make_panel(content, width=215)
        high_panel.grid(row=0, column=3, sticky="nsew")
        high_panel.grid_propagate(False)

        tk.Label(
            high_panel,
            text="HIGH SCORES",
            font=("Segoe UI", 13, "bold"),
            fg="#aab5c5",
            bg="#192231",
        ).pack(pady=(20, 8))

        self.high_score_list = tk.Listbox(
            high_panel,
            font=("Consolas", 11),
            bg="#101722",
            fg="#ffd66b",
            selectbackground="#31415a",
            relief="flat",
            highlightthickness=0,
            activestyle="none",
        )
        self.high_score_list.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    @staticmethod
    def make_panel(parent: tk.Widget, width: int | None = None) -> tk.Frame:
        kwargs: dict[str, object] = {"bg": "#192231", "bd": 0}
        if width is not None:
            kwargs["width"] = width
        return tk.Frame(parent, **kwargs)

    def open_word_list(self) -> None:
        """Open the editable allow/block list in the operating system's editor."""
        self.validator.ensure_override_file()
        try:
            if sys.platform.startswith("win"):
                os.startfile(WORD_OVERRIDE_FILE)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(WORD_OVERRIDE_FILE)])
            else:
                subprocess.Popen(["xdg-open", str(WORD_OVERRIDE_FILE)])
            self.status_label.config(text="Word list opened • changes load automatically")
        except Exception as exc:
            messagebox.showerror(
                "Could Not Open Word List",
                f"Open this file manually:\n{WORD_OVERRIDE_FILE}\n\n{exc}",
                parent=self,
            )

    # ---------- Game flow ----------

    def start_game(self) -> None:
        self.cancel_scheduled_callbacks()
        self.game_active = True
        self.reels_spinning = False
        self.reels_stopping = False
        self.time_left = STARTING_TIME
        self.score = 0
        self.used_words.clear()
        self.round_words.clear()
        self.current_secret_word = ""
        self.words_list.delete(0, tk.END)
        self.feedback_label.config(text="")
        self.start_button.config(text="RESTART")
        self.update_score_display()
        self.update_timer_display()
        self.last_tick = time.monotonic()
        self.timer_loop()
        self.begin_round()

    def minimum_words_for_score(self) -> int:
        """Gradually introduce rarer letter pairs as the score increases."""
        if self.score < 2500:
            return 150
        if self.score < 5000:
            return 75
        if self.score < 10000:
            return 40
        return 15

    def begin_round(self) -> None:
        if not self.game_active:
            return

        self.word_var.set("")
        self.word_entry.config(state="disabled")
        self.submit_button.config(state="disabled")
        self.feedback_label.config(text="")
        self.prompt_label.config(text="Word: ? ... ?")
        self.status_label.config(text="Reels spinning")

        try:
            first, last, word_count, secret_word = self.validator.choose_valid_pair(
                self.minimum_words_for_score()
            )
        except RuntimeError as exc:
            self.game_active = False
            self.spin_button.config(state="disabled")
            self.status_label.config(text="Dictionary unavailable")
            messagebox.showerror("No Playable Words", str(exc), parent=self)
            return

        self.target_letters = [first, last]
        self.current_pair_word_count = word_count
        self.current_secret_word = secret_word
        self.reel_letters = [self.make_reel(), self.make_reel()]
        self.reel_offsets = [0.0, 0.0]
        self.reel_speeds = [720.0, 810.0]
        self.reels_spinning = True
        self.reels_stopping = False
        self.spin_button.config(state="normal", text="STOP LETTERS")
        self.last_animation_time = time.monotonic()
        self.animate_reels()

    def handle_space_key(self, event: tk.Event) -> str:
        """Use Space to stop spinning reels without inserting a space in the entry."""
        if self.game_active and self.reels_spinning and not self.reels_stopping:
            self.stop_reels()
        elif not self.game_active:
            self.start_game()
        return "break"

    def handle_enter_key(self, event: tk.Event) -> str:
        """Enter stops reels, submits a word, or starts a new game based on state."""
        if not self.game_active:
            self.start_game()
        elif self.reels_spinning and not self.reels_stopping:
            self.stop_reels()
        elif not self.reels_spinning:
            self.submit_word()
        return "break"

    def stop_reels(self) -> None:
        if not self.game_active or not self.reels_spinning or self.reels_stopping:
            return

        self.reels_stopping = True
        self.spin_button.config(state="disabled", text="STOPPING...")
        self.status_label.config(text="Locking letters")
        self.pending_stops = 2

        self.after(350, lambda: self.decelerate_reel(0))
        self.after(850, lambda: self.decelerate_reel(1))

    def decelerate_reel(self, reel_index: int) -> None:
        if not self.game_active:
            return
        self.slow_reel(reel_index)

    def slow_reel(self, reel_index: int) -> None:
        if not self.game_active:
            return

        self.reel_speeds[reel_index] *= 0.82
        if self.reel_speeds[reel_index] > 35:
            self.after(55, lambda: self.slow_reel(reel_index))
            return

        self.reel_speeds[reel_index] = 0.0
        self.snap_reel(reel_index)

    def snap_reel(self, reel_index: int) -> None:
        self.reel_offsets[reel_index] = 0.0
        chosen = self.target_letters[reel_index]
        self.reel_letters[reel_index][CENTER_INDEX] = chosen
        self.selected_letters[reel_index] = chosen
        self.pending_stops -= 1
        self.draw_reels()

        if self.pending_stops == 0:
            self.reels_spinning = False
            self.reels_stopping = False
            self.on_letters_selected()

    def on_letters_selected(self) -> None:
        first, last = self.selected_letters
        self.prompt_label.config(text=f"Word: {first} ... {last}")
        self.status_label.config(text=f"Use {first} and {last} • {self.current_pair_word_count:,} possible")
        self.spin_button.config(state="disabled", text="LETTERS LOCKED")
        self.word_entry.config(state="normal")
        self.submit_button.config(state="normal")
        self.word_entry.focus_set()

    def submit_word(self) -> None:
        if not self.game_active or self.reels_spinning:
            return

        word = self.word_var.get().strip().lower()
        first, last = (letter.lower() for letter in self.selected_letters)

        if not word:
            self.show_feedback("Enter a word first.")
            return
        if not word.isalpha():
            self.show_feedback("Letters only, please.")
            return
        if len(word) < 2:
            self.show_feedback("The word must contain at least two letters.")
            return
        if not word.startswith(first) or not word.endswith(last):
            self.show_feedback(
                f"The word must begin with {first.upper()} and end with {last.upper()}."
            )
            return
        if word in self.used_words:
            self.show_feedback("That word has already been used.")
            return
        if not self.validator.is_word(word):
            self.show_feedback("Not in the American English dictionary or your allow list.")
            return

        self.used_words.add(word)
        self.round_words.append(word)
        self.score += POINTS_PER_WORD
        self.time_left += TIME_BONUS
        self.words_list.insert(tk.END, f"{len(self.round_words):02}.  {word.upper()}")
        self.words_list.see(tk.END)
        self.update_score_display()
        self.update_timer_display()
        self.feedback_label.config(text=f"Accepted! +{TIME_BONUS} seconds • {self.current_pair_word_count:,} possible words", fg="#78f0b2")
        self.status_label.config(text="Correct")
        self.word_entry.config(state="disabled")
        self.submit_button.config(state="disabled")
        self.after(700, self.begin_round)

    def end_game(self) -> None:
        if not self.game_active:
            return

        self.game_active = False
        self.reels_spinning = False
        self.reels_stopping = False
        self.cancel_scheduled_callbacks()
        self.time_left = 0
        self.update_timer_display()
        self.spin_button.config(state="disabled", text="GAME OVER")
        self.word_entry.config(state="disabled")
        self.submit_button.config(state="disabled")
        reveal = self.current_secret_word.upper() if self.current_secret_word else "A VALID WORD"
        self.status_label.config(
            text=f"TIME EXPIRED\n\nYou could have said:\n{reveal}",
            fg="#ffd66b",
        )
        self.feedback_label.config(
            text=f"Final score: {self.score}. Press Start, Space, or Enter to play again.",
            fg="#ffd66b",
        )
        self.start_button.config(text="START AGAIN")

        if self.score > 0:
            name = simpledialog.askstring(
                "Save High Score",
                f"Final score: {self.score}\nEnter your name:",
                parent=self,
            )
            self.add_high_score((name or "PLAYER").strip()[:18] or "PLAYER", self.score)

    # ---------- Animation ----------

    def make_reel(self) -> list[str]:
        weights = "EEEEEEEEAAAAAAAIIIIIIIOOOOOONNNNNNRRRRRRTTTTTTLLLLSSSSUUUUDDDDGGGBBCCMMPPFFHHVVWWYYKJXQZ"
        return [random.choice(weights) for _ in range(REEL_LENGTH)]

    def animate_reels(self) -> None:
        if not self.game_active or not self.reels_spinning:
            return

        now = time.monotonic()
        dt = min(now - self.last_animation_time, 0.05)
        self.last_animation_time = now

        cell_width = self.reel_cell_width()
        for i in range(2):
            if self.reel_speeds[i] <= 0:
                continue
            self.reel_offsets[i] += self.reel_speeds[i] * dt
            while self.reel_offsets[i] >= cell_width:
                self.reel_offsets[i] -= cell_width
                self.reel_letters[i].pop(0)
                self.reel_letters[i].append(self.make_reel()[0])

        self.draw_reels()
        self.animation_after_id = self.after(16, self.animate_reels)

    def reel_cell_width(self) -> float:
        width = max(self.reel_canvas.winfo_width(), 600)
        return width / 9.0

    def draw_reels(self) -> None:
        canvas = self.reel_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        cell_width = self.reel_cell_width()
        center_x = width / 2

        reel_y_positions = [75, 185]
        for reel_index, center_y in enumerate(reel_y_positions):
            canvas.create_rectangle(
                18,
                center_y - 43,
                width - 18,
                center_y + 43,
                fill="#121b28",
                outline="#2a384d",
                width=2,
            )

            start_x = center_x - CENTER_INDEX * cell_width - self.reel_offsets[reel_index]
            for idx, letter in enumerate(self.reel_letters[reel_index]):
                x = start_x + idx * cell_width
                distance = abs(x - center_x)
                if distance > width / 2 + cell_width:
                    continue
                brightness = max(95, int(235 - distance * 0.42))
                color = f"#{brightness:02x}{min(255, brightness + 7):02x}{min(255, brightness + 15):02x}"
                canvas.create_text(
                    x,
                    center_y,
                    text=letter,
                    font=("Consolas", 31, "bold"),
                    fill=color,
                )

            # Selection square and center indicator line
            canvas.create_rectangle(
                center_x - cell_width * 0.43,
                center_y - 39,
                center_x + cell_width * 0.43,
                center_y + 39,
                outline="#ffd66b",
                width=4,
            )
            canvas.create_line(
                center_x,
                center_y - 51,
                center_x,
                center_y - 40,
                fill="#ffd66b",
                width=4,
            )

        canvas.create_text(
            center_x,
            height / 2,
            text="FIRST LETTER" if self.reels_spinning else "SELECTED LETTERS",
            font=("Segoe UI", 9, "bold"),
            fill="#5f7189",
        )

    # ---------- Timer ----------

    def timer_loop(self) -> None:
        if not self.game_active:
            return

        now = time.monotonic()
        elapsed = now - self.last_tick
        self.last_tick = now
        self.time_left = max(0.0, self.time_left - elapsed)
        self.update_timer_display()

        if self.time_left <= 0:
            self.end_game()
            return

        self.timer_after_id = self.after(100, self.timer_loop)

    def update_timer_display(self) -> None:
        total = max(0, int(self.time_left + 0.999))
        minutes, seconds = divmod(total, 60)
        self.timer_label.config(text=f"{minutes:02}:{seconds:02}")
        if total <= 10:
            self.timer_label.config(fg="#ff7186")
        elif total <= 30:
            self.timer_label.config(fg="#ffd66b")
        else:
            self.timer_label.config(fg="#78f0b2")

    def update_score_display(self) -> None:
        self.score_label.config(text=f"{self.score:,}")

    def show_feedback(self, message: str) -> None:
        self.feedback_label.config(text=message, fg="#ff8a9a")
        self.word_entry.focus_set()
        self.word_entry.selection_range(0, tk.END)

    # ---------- High scores ----------

    def load_high_scores(self) -> list[dict[str, object]]:
        try:
            data = json.loads(HIGH_SCORE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                cleaned = []
                for item in data:
                    if isinstance(item, dict) and "name" in item and "score" in item:
                        cleaned.append(
                            {
                                "name": str(item["name"])[:18],
                                "score": int(item["score"]),
                            }
                        )
                return sorted(cleaned, key=lambda x: int(x["score"]), reverse=True)[
                    :HIGH_SCORE_LIMIT
                ]
        except Exception:
            pass
        return []

    def add_high_score(self, name: str, score: int) -> None:
        self.high_scores.append({"name": name.upper(), "score": score})
        self.high_scores.sort(key=lambda item: int(item["score"]), reverse=True)
        self.high_scores = self.high_scores[:HIGH_SCORE_LIMIT]
        try:
            HIGH_SCORE_FILE.write_text(
                json.dumps(self.high_scores, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            messagebox.showwarning(
                "High Score",
                f"The score could not be saved:\n{exc}",
                parent=self,
            )
        self.refresh_high_scores()

    def refresh_high_scores(self) -> None:
        self.high_score_list.delete(0, tk.END)
        if not self.high_scores:
            self.high_score_list.insert(tk.END, "No scores yet")
            return
        for rank, item in enumerate(self.high_scores, start=1):
            name = str(item["name"])[:12]
            score = int(item["score"])
            self.high_score_list.insert(
                tk.END, f"{rank:>2}. {name:<12} {score:>5}"
            )

    # ---------- Cleanup ----------

    def cancel_scheduled_callbacks(self) -> None:
        for callback_id in (self.timer_after_id, self.animation_after_id):
            if callback_id:
                try:
                    self.after_cancel(callback_id)
                except tk.TclError:
                    pass
        self.timer_after_id = None
        self.animation_after_id = None

    def on_close(self) -> None:
        self.cancel_scheduled_callbacks()
        self.destroy()


if __name__ == "__main__":
    app = SpinnyLettersGame()
    app.mainloop()
