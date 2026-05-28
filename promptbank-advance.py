#!/usr/bin/env python3
"""
Prompt Bank Advance — Ultra-minimal colorful TUI for LLM prompts with AI assistance.

Termux-friendly. Vanilla Python + curses. No dependencies.
No tags, no sort, no filter, no timestamps, no usage counters.
Just a clean list of titles + previews, and a detail view.

PLUS: Two-step AI code generation workflow:
  Step 1: Llama-3.2-3B-Instruct-GGUF:Q4_K_M transforms user request into optimized prompt
  Step 2: nanbeige4.1-python-deepthink:3b generates actual Python code

Keys:
  j/k      navigate
  l/Enter  open detail
  h/q/Esc  back / quit
  y        yank (copy) prompt to clipboard
  a        add prompt
  e        edit prompt
  d        delete prompt
  E        export all to JSON
  ?        help
  Ctrl-L   redraw
  
AI Features:
  c        generate code with AI (two-step process)
  r        refine selected prompt with Llama
"""

import base64
import curses
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path.home() / ".promptbank"
DATA_FILE = DATA_DIR / "prompts.json"

# LLM Model configuration for two-step process
LLAMA_MODEL = "Llama-3.2-3B-Instruct-GGUF:Q4_K_M"
NANBEIGE_MODEL = "nanbeige4.1-python-deepthink:3b"


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def load_prompts():
    """Load prompt list from JSON. Returns empty list on any error."""
    if not DATA_FILE.exists():
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_prompts(prompts):
    """Atomic write: dump to temp file, then move into place."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prompts, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DATA_FILE)


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

def copy_clip(text):
    """
    Copy text to system clipboard.

    Tries two methods in order:
      1. OSC 52 escape sequence (works in Termux, tmux, SSH, modern terminals)
      2. termux-clipboard-set binary (Termux native)

    Returns (success: bool, method: str).
    """
    methods = []

    # OSC 52 — universal terminal clipboard protocol
    try:
        b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        sys.stdout.write(f"\033]52;c;{b64}\a")
        sys.stdout.flush()
        methods.append("OSC52")
    except Exception:
        pass

    # Termux native binary
    try:
        subprocess.run(
            ["termux-clipboard-set"],
            input=text,
            text=True,
            check=True,
            capture_output=True,
            timeout=3,
        )
        methods.append("termux")
    except Exception:
        pass

    if methods:
        return True, "+".join(methods)
    return False, "no clipboard method"


# ---------------------------------------------------------------------------
# External editor ($EDITOR / nano / vi)
# ---------------------------------------------------------------------------

def run_editor(initial=""):
    """
    Suspend curses, open the user's $EDITOR on a temp file, resume curses.
    Returns the edited text, or None if the editor failed or was cancelled.
    """
    editor = os.environ.get("EDITOR", "nano")

    # Fallback to vi if the preferred editor isn't on PATH
    try:
        subprocess.run(["which", editor], check=True, capture_output=True)
    except Exception:
        editor = "vi"

    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(initial)
        tmp = f.name

    # Drop out of curses so the editor owns the terminal
    curses.endwin()

    content = None
    try:
        subprocess.run([editor, tmp], check=False)
        with open(tmp, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"editor error: {e}", file=sys.stderr)
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass

    return content


# ---------------------------------------------------------------------------
# LLM Integration - Two-Step Process
# ---------------------------------------------------------------------------

def call_llm(model: str, prompt: str, system_prompt: str = "") -> tuple[bool, str]:
    """
    Call an LLM via ollama CLI and return (success, response).
    
    Args:
        model: Model name to use
        prompt: User prompt
        system_prompt: Optional system prompt
    
    Returns:
        Tuple of (success: bool, response_or_error: str)
    """
    cmd = ["ollama", "run", model]
    
    full_prompt = prompt
    if system_prompt:
        full_prompt = f"System: {system_prompt}\n\nUser: {prompt}"
    
    try:
        result = subprocess.run(
            cmd,
            input=full_prompt,
            text=True,
            capture_output=True,
            timeout=300,  # 5 minute timeout for code generation
        )
        
        if result.returncode == 0:
            return True, result.stdout.strip()
        else:
            return False, f"LLM error: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "LLM timeout after 5 minutes"
    except FileNotFoundError:
        return False, "ollama not found in PATH"
    except Exception as e:
        return False, f"LLM call failed: {str(e)}"


def transform_with_llama(user_request: str) -> tuple[bool, str]:
    """
    Step 1: Use Llama-3.2-3B-Instruct-GGUF:Q4_K_M to transform
    user request into a clear, well-planned, compact prompt
    optimized for nanbeige4.1-python-deepthink:3b.
    
    Returns (success, transformed_prompt_or_error).
    """
    system_prompt = """You are a prompt engineering expert. Your task is to transform vague user requests into clear, well-structured, precise prompts optimized for a code-generation AI (nanbeige4.1-python-deepthink:3b).

Your output should:
1. Clearly state the goal
2. Specify required functionality
3. List any constraints or requirements
4. Be compact but complete
5. Include examples if helpful

Output ONLY the transformed prompt, nothing else. No explanations, no meta-commentary."""

    prompt = f"""Transform this user request into an optimized prompt for Python code generation:

{user_request}

Provide only the optimized prompt:"""

    return call_llm(LLAMA_MODEL, prompt, system_prompt)


def generate_code_with_nanbeige(optimized_prompt: str) -> tuple[bool, str]:
    """
    Step 2: Use nanbeige4.1-python-deepthink:3b to generate
    actual Python code based on the optimized prompt.
    
    Returns (success, code_or_error).
    """
    system_prompt = """You are an expert Python programmer. Generate clean, well-structured, production-ready Python code.

Requirements:
- Write complete, working code
- Include necessary imports
- Add clear comments where helpful
- Follow Python best practices
- Handle edge cases appropriately
- Output code in a markdown code block

Always wrap your code in ```python ... ``` blocks."""

    return call_llm(NANBEIGE_MODEL, optimized_prompt, system_prompt)


def extract_code_block(text: str) -> str:
    """Extract Python code from a markdown code block in the response."""
    lines = text.split('\n')
    in_code_block = False
    code_lines = []
    
    for line in lines:
        if line.strip().startswith('```python') or line.strip() == '```':
            if not in_code_block:
                in_code_block = True
                continue
            else:
                break
        elif in_code_block:
            code_lines.append(line)
    
    if code_lines:
        return '\n'.join(code_lines)
    
    # Fallback: return entire response if no code block found
    return text


def two_step_code_generation(user_request: str, callback=None) -> tuple[bool, str]:
    """
    Execute the full two-step code generation process:
    1. Llama transforms the request
    2. Nanbeige generates the code
    
    Returns (success, generated_code_or_error).
    """
    # Step 1: Transform with Llama
    if callback:
        callback("Step 1/2: Transforming request with Llama...")
    
    success, result = transform_with_llama(user_request)
    if not success:
        return False, f"Step 1 failed: {result}"
    
    optimized_prompt = result
    
    # Step 2: Generate code with Nanbeige
    if callback:
        callback("Step 2/2: Generating code with Nanbeige...")
    
    success, result = generate_code_with_nanbeige(optimized_prompt)
    if not success:
        return False, f"Step 2 failed: {result}"
    
    # Extract code from response
    code = extract_code_block(result)
    
    if callback:
        callback("Code generation complete!")
    
    return True, code


# ---------------------------------------------------------------------------
# Curses application
# ---------------------------------------------------------------------------

class App:
    """Main TUI state machine. Modes: LIST | DETAIL | HELP | INPUT | CONFIRM | GENERATING."""

    def __init__(self, stdscr):
        self.s = stdscr          # main curses window
        self.prompts = load_prompts()
        self.sel = 0             # cursor index in LIST mode
        self.scroll = 0          # first visible row in LIST mode
        self.mode = "LIST"       # current UI mode
        self.status = ""         # ephemeral status message
        self.status_err = False  # red if True, green if False
        self.status_until = 0    # timestamp when status should disappear

        # Modal state
        self._input_prompt = ""   # label shown above input buffer
        self._input_buf = ""      # characters typed by user
        self._input_cb = None     # function to call on Enter
        self._confirm_msg = ""    # question shown in CONFIRM mode
        self._confirm_cb = None   # function to call on "y"
        self._detail_idx = 0      # which prompt is open in DETAIL mode
        self._tmp_id = ""         # id of prompt being edited
        
        # AI generation state
        self._generating = False
        self._gen_status = ""
        self._gen_result = None

        self._init_colors()
        self.s.keypad(True)       # enable arrow keys, function keys
        self.s.nodelay(False)     # blocking getch (no busy-wait)
        curses.curs_set(0)        # hide cursor by default

    # ── Color setup ────────────────────────────────────────────────────────

    def _init_colors(self):
        """Define 9 color pairs for a bright, readable UI."""
        if not curses.has_colors():
            return
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)                # header
        curses.init_pair(2, curses.COLOR_GREEN, -1)               # titles
        curses.init_pair(3, curses.COLOR_YELLOW, -1)              # index numbers
        curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_CYAN)  # selected row
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)             # preview text
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_YELLOW)  # status bar
        curses.init_pair(7, curses.COLOR_RED, -1)                 # errors
        curses.init_pair(8, curses.COLOR_WHITE, -1)               # plain body text
        curses.init_pair(9, curses.COLOR_BLUE, -1)                # separators / borders

    # ── Safe drawing helper ───────────────────────────────────────────────

    def _add(self, y, x, text, attr=0):
        """
        Draw text at (y, x) without crashing on edge cases.
        Truncates if text would exceed window width.
        """
        h, w = self.s.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        if x < 0:
            text = text[-x:]
            x = 0
        room = w - x - 1
        if room <= 0:
            return
        if len(text) > room:
            text = text[:room]
        try:
            self.s.addstr(y, x, text, attr)
        except curses.error:
            pass

    # ── Status message ────────────────────────────────────────────────────

    def _set_status(self, msg, err=False, secs=2):
        """Show a message in the status bar for `secs` seconds."""
        self.status = msg
        self.status_err = err
        self.status_until = __import__("time").time() + secs

    # ── Scroll clamping ───────────────────────────────────────────────────

    def _clamp(self):
        """Keep cursor inside list bounds and scroll window synced."""
        if self.prompts:
            self.sel = max(0, min(self.sel, len(self.prompts) - 1))
        else:
            self.sel = 0

        h = self.s.getmaxyx()[0]
        visible = max(1, h - 3)  # rows reserved for header + two footer lines

        if self.sel < self.scroll:
            self.scroll = self.sel
        elif self.sel >= self.scroll + visible:
            self.scroll = self.sel - visible + 1

        self.scroll = max(0, self.scroll)
        if self.prompts and self.scroll > len(self.prompts) - visible:
            self.scroll = max(0, len(self.prompts) - visible)

    # ── Main draw dispatcher ───────────────────────────────────────────────

    def draw(self):
        """Erase screen and redraw everything for the current mode."""
        self.s.erase()
        h, w = self.s.getmaxyx()

        if h < 6 or w < 30:
            self._add(0, 0, "Terminal too small", curses.A_BOLD)
            self.s.refresh()
            return

        {
            "LIST":       self._draw_list,
            "DETAIL":     self._draw_detail,
            "HELP":       self._draw_help,
            "INPUT":      self._draw_input,
            "CONFIRM":    self._draw_confirm,
            "GENERATING": self._draw_generating,
        }[self.mode](h, w)

        self.s.refresh()

    # ── LIST mode ─────────────────────────────────────────────────────────

    def _draw_list(self, h, w):
        # Header bar
        hdr = f" 📦  PROMPT BANK ADVANCE   {len(self.prompts)} prompts "
        self._add(0, 0, hdr.ljust(w - 1), curses.color_pair(1) | curses.A_BOLD)
        self._add(0, len(hdr), " " * (w - len(hdr) - 1), curses.color_pair(1) | curses.A_BOLD)

        # Prompt rows
        visible = h - 3
        for i in range(visible):
            idx = self.scroll + i
            if idx >= len(self.prompts):
                break

            p = self.prompts[idx]
            y = 1 + i
            num = f"{idx + 1:>3}"
            title = p.get("title", "Untitled")[:28]

            # First line of content as inline preview
            content = p.get("content", "").replace("\n", " ")
            preview = content[:w - 32]
            if len(preview) > w - 32:
                preview = preview[:w - 35] + "..."

            is_sel = (idx == self.sel)

            if is_sel:
                # Full-width cyan highlight bar
                self._add(y, 0, " " * (w - 1), curses.color_pair(4))
                self._add(y, 1, num,  curses.color_pair(4) | curses.A_BOLD)
                self._add(y, 6, title, curses.color_pair(4) | curses.A_BOLD)
                if preview:
                    self._add(y, 36, preview, curses.color_pair(4))
            else:
                self._add(y, 1, num,  curses.color_pair(3))
                self._add(y, 6, title, curses.color_pair(2) | curses.A_BOLD)
                if preview:
                    self._add(y, 36, preview, curses.color_pair(5))

        if not self.prompts:
            self._add(2, 2, "(empty — press 'a' to add a prompt)", curses.A_DIM)

        # Status / hint bar
        now = __import__("time").time()
        if self.status and now < self.status_until:
            attr = curses.color_pair(7) | curses.A_BOLD if self.status_err else curses.color_pair(8) | curses.A_BOLD
            self._add(h - 2, 0, self.status[:w], attr)
        else:
            bar = " j/k:nav  l:open  y:yank  a:add  e:edit  d:del  c:AI-code  E:export  ?:help  q:quit "
            self._add(h - 2, 0, bar[:w], curses.color_pair(6) | curses.A_BOLD)

        # Bottom hint line
        hint = " Enter:view  h:back  Ctrl-L:redraw "
        self._add(h - 1, 0, hint[:w], curses.A_DIM)

    # ── DETAIL mode ───────────────────────────────────────────────────────

    def _draw_detail(self, h, w):
        if not self.prompts or self._detail_idx >= len(self.prompts):
            self._add(0, 0, "No prompt", curses.A_BOLD)
            return

        p = self.prompts[self._detail_idx]

        # Title line
        title = f" {p.get('title', 'Untitled')} "
        self._add(0, 0, title.ljust(w - 1), curses.color_pair(2) | curses.A_BOLD)

        # Separator
        sep = "─" * (w - 1)
        self._add(1, 0, sep, curses.color_pair(9))

        # Content body
        y = 2
        for line in p.get("content", "").splitlines():
            if y >= h - 2:
                self._add(h - 3, 0, "...", curses.A_DIM)
                break
            while line and y < h - 2:
                chunk = line[:w - 1]
                self._add(y, 0, chunk, curses.color_pair(8))
                y += 1
                line = line[w - 1:]
            if not line and y < h - 2:
                y += 1

        # Footer
        foot = " h:back  j/k:prev/next  y:yank  e:edit  d:delete  c:AI-generate  q:quit "
        self._add(h - 2, 0, foot[:w], curses.color_pair(6) | curses.A_BOLD)

    # ── HELP overlay ──────────────────────────────────────────────────────

    def _draw_help(self, h, w):
        lines = [
            "",
            "     PROMPT BANK ADVANCE — KEYS",
            "",
            "  j / k        move down / up",
            "  l / Enter    open prompt detail",
            "  h / q / Esc  back / quit / cancel",
            "",
            "  y            yank (copy) to clipboard",
            "  a            add new prompt",
            "  e            edit prompt",
            "  d            delete prompt",
            "",
            "  c            AI code generation (two-step)",
            "               Step 1: Llama optimizes prompt",
            "               Step 2: Nanbeige generates code",
            "",
            "  E            export all to JSON",
            "  ?            this help",
            "  Ctrl-L       force redraw",
            "",
            "  Press any key to close",
            "",
        ]
        box_h = len(lines)
        box_w = max(len(l) for l in lines) + 2
        by = max(0, (h - box_h) // 2)
        bx = max(0, (w - box_w) // 2)

        # Box background
        for i, line in enumerate(lines):
            yy = by + i
            if 0 <= yy < h:
                pad = " " * (box_w - len(line))
                self._add(yy, bx, line + pad, curses.color_pair(9) | curses.A_BOLD)

        # Side borders
        for yy in range(by, min(by + box_h, h)):
            self._add(yy, bx - 1, "│", curses.color_pair(1))
            self._add(yy, bx + box_w, "│", curses.color_pair(1))

        # Top & bottom borders
        if by > 0:
            self._add(by - 1, bx - 1, "┌" + "─" * box_w + "┐", curses.color_pair(1))
        end = min(by + box_h, h - 1)
        self._add(end, bx - 1, "└" + "─" * box_w + "┘", curses.color_pair(1))

    # ── INPUT mode (bottom-line text entry) ───────────────────────────────

    def _draw_input(self, h, w):
        prompt = f" {self._input_prompt}: "
        y = h // 2
        x = max(0, (w - len(prompt) - 40) // 2)
        self._add(y, x, prompt, curses.color_pair(2) | curses.A_BOLD)
        self._add(y, x + len(prompt), self._input_buf, curses.color_pair(8))
        self._add(h - 1, 0, " Enter:confirm  Esc:cancel ", curses.A_DIM)
        self.s.move(y, x + len(prompt) + len(self._input_buf))

    # ── CONFIRM mode (yes/no popup) ───────────────────────────────────────

    def _draw_confirm(self, h, w):
        msg = f" {self._confirm_msg} (y/n) "
        y = h // 2
        x = max(0, (w - len(msg)) // 2)
        self._add(y, x, msg, curses.color_pair(4) | curses.A_BOLD | curses.A_REVERSE)
        self._add(h - 1, 0, " y:yes  n:no  Esc:cancel ", curses.A_DIM)

    # ── GENERATING mode (AI code generation progress) ─────────────────────

    def _draw_generating(self, h, w):
        # Title
        title = " 🤖  AI CODE GENERATION "
        self._add(0, 0, title.ljust(w - 1), curses.color_pair(1) | curses.A_BOLD)

        # Separator
        sep = "─" * (w - 1)
        self._add(1, 0, sep, curses.color_pair(9))

        # Progress info
        y = 3
        self._add(y, 2, "Two-step process:", curses.color_pair(2) | curses.A_BOLD)
        y += 2
        
        self._add(y, 4, "Step 1: Llama-3.2-3B-Instruct-GGUF:Q4_K_M", curses.color_pair(3))
        self._add(y + 1, 6, "→ Transforms your request into optimized prompt", curses.A_DIM)
        y += 3
        
        self._add(y, 4, "Step 2: nanbeige4.1-python-deepthink:3b", curses.color_pair(5))
        self._add(y + 1, 6, "→ Generates actual Python code", curses.A_DIM)
        y += 3

        # Current status
        self._add(y + 1, 2, f"Status: {self._gen_status}", curses.color_pair(8) | curses.A_BOLD)

        # Hint
        hint = " Press any key to cancel "
        self._add(h - 2, 0, hint[:w], curses.A_DIM)

    # ── Main loop ──────────────────────────────────────────────────────────

    def run(self):
        """Block on keyboard input, dispatch, redraw. Repeat until quit."""
        self.draw()
        while True:
            key = self.s.getch()
            if not self._handle(key):
                break
            self.draw()

    def _handle(self, key):
        """Route a keypress to the correct handler for the current mode."""
        if key == -1:
            return True

        handlers = {
            "LIST":       self._h_list,
            "DETAIL":     self._h_detail,
            "HELP":       self._h_help,
            "INPUT":      self._h_input,
            "CONFIRM":    self._h_confirm,
            "GENERATING": self._h_generating,
        }
        return handlers[self.mode](key)

    # ── LIST mode handler ────────────────────────────────────────────────

    def _h_list(self, key):
        if key == ord("q"):
            return False

        elif key in (ord("j"), curses.KEY_DOWN):
            if self.sel < len(self.prompts) - 1:
                self.sel += 1
                self._clamp()

        elif key in (ord("k"), curses.KEY_UP):
            if self.sel > 0:
                self.sel -= 1
                self._clamp()

        elif key == ord("g"):
            self.sel = 0
            self.scroll = 0

        elif key == ord("G"):
            self.sel = max(0, len(self.prompts) - 1)
            self._clamp()

        elif key in (ord("l"), 10, 13):
            if self.prompts:
                self._detail_idx = self.sel
                self.mode = "DETAIL"

        elif key == ord("y"):
            self._yank(self.sel)

        elif key == ord("a"):
            self._start_input("Title", self._add_prompt)

        elif key == ord("e"):
            if self.prompts:
                self._edit(self.sel)

        elif key == ord("d"):
            if self.prompts:
                self._confirm("Delete this prompt?", self._do_delete)

        elif key == ord("c"):
            self._start_ai_generation()

        elif key == ord("E"):
            self._export()

        elif key in (ord("?"), ord("/")):
            self.mode = "HELP"

        elif key == 12:  # Ctrl-L
            self.s.clear()
            self.s.refresh()

        elif key == curses.KEY_RESIZE:
            self._clamp()

        return True

    # ── DETAIL mode handler ───────────────────────────────────────────────

    def _h_detail(self, key):
        if key in (ord("h"), ord("q"), 27):
            self.sel = self._detail_idx
            self.mode = "LIST"
            self._clamp()

        elif key in (ord("j"), curses.KEY_DOWN):
            if self._detail_idx < len(self.prompts) - 1:
                self._detail_idx += 1

        elif key in (ord("k"), curses.KEY_UP):
            if self._detail_idx > 0:
                self._detail_idx -= 1

        elif key == ord("y"):
            self._yank(self._detail_idx)

        elif key == ord("e"):
            self.sel = self._detail_idx
            self._edit(self._detail_idx)

        elif key == ord("d"):
            self._confirm("Delete this prompt?", self._do_delete_detail)

        elif key == ord("c"):
            self._start_ai_generation()

        return True

    # ── HELP mode handler ─────────────────────────────────────────────────

    def _h_help(self, _key):
        self.mode = "LIST"
        return True

    # ── INPUT mode handler ────────────────────────────────────────────────

    def _h_input(self, key):
        if key == 27:  # Esc — cancel
            self.mode = "LIST"
            self._input_buf = ""
            curses.curs_set(0)

        elif key in (10, 13):  # Enter — confirm
            text = self._input_buf
            cb = self._input_cb
            self.mode = "LIST"
            self._input_buf = ""
            self._input_cb = None
            curses.curs_set(0)
            if cb:
                cb(text)

        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self._input_buf = self._input_buf[:-1]

        elif 32 <= key <= 126:
            self._input_buf += chr(key)

        return True

    # ── CONFIRM mode handler ──────────────────────────────────────────────

    def _h_confirm(self, key):
        if key in (ord("y"), ord("Y")):
            self.mode = "LIST"
            if self._confirm_cb:
                self._confirm_cb()
        elif key in (ord("n"), ord("N"), 27):
            self.mode = "LIST"
        return True

    # ── GENERATING mode handler ───────────────────────────────────────────

    def _h_generating(self, key):
        """Any key cancels the generation."""
        self.mode = "LIST"
        self._generating = False
        self._set_status("AI generation cancelled")
        return True

    # ── Actions ───────────────────────────────────────────────────────────

    def _yank(self, idx):
        """Copy prompt content to clipboard and flash a status message."""
        if not (0 <= idx < len(self.prompts)):
            return
        p = self.prompts[idx]
        ok, method = copy_clip(p.get("content", ""))
        if ok:
            self._set_status(f"Yanked ({method})")
        else:
            self._set_status(f"Clipboard failed: {method}", err=True)

    def _add_prompt(self, title):
        """Prompt for title, open editor, append new prompt to list."""
        if not title or not title.strip():
            self._set_status("Add cancelled")
            return

        body = run_editor("")
        if body is not None:
            self.prompts.append({
                "id": str(uuid.uuid4()),
                "title": title.strip(),
                "content": body,
            })
            save_prompts(self.prompts)
            self.sel = len(self.prompts) - 1
            self._clamp()
            self._set_status("Prompt added")
        else:
            self._set_status("Add cancelled")

    def _edit(self, idx):
        """Start editing the prompt at `idx`."""
        if not (0 <= idx < len(self.prompts)):
            return
        p = self.prompts[idx]
        self._tmp_id = p["id"]
        self._start_input("Title", self._edit_title, p.get("title", ""))

    def _edit_title(self, title):
        """Receive new title, open editor with old content, save changes."""
        if not title or not title.strip():
            self._set_status("Edit cancelled")
            return

        orig = next((p for p in self.prompts if p["id"] == self._tmp_id), None)
        if not orig:
            self._set_status("Not found", err=True)
            return

        body = run_editor(orig.get("content", ""))
        if body is not None:
            orig["title"] = title.strip()
            orig["content"] = body
            save_prompts(self.prompts)
            self._set_status("Prompt updated")
        else:
            self._set_status("Edit cancelled")

    def _do_delete(self):
        """Delete the currently selected prompt (LIST mode)."""
        if 0 <= self.sel < len(self.prompts):
            del self.prompts[self.sel]
            save_prompts(self.prompts)
            self._clamp()
            self._set_status("Deleted")

    def _do_delete_detail(self):
        """Delete the prompt open in DETAIL mode, then return to LIST."""
        if 0 <= self._detail_idx < len(self.prompts):
            del self.prompts[self._detail_idx]
            save_prompts(self.prompts)
            self.sel = min(self._detail_idx, len(self.prompts) - 1)
            self.mode = "LIST"
            self._clamp()
            self._set_status("Deleted")

    def _export(self):
        """Dump the entire prompt list to a timestamped JSON file in cwd."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"prompt_bank_{ts}.json"
        try:
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(self.prompts, f, indent=2, ensure_ascii=False)
            self._set_status(f"Exported {len(self.prompts)} → {fname}")
        except Exception as e:
            self._set_status(f"Export failed: {e}", err=True)

    def _start_ai_generation(self):
        """Start the two-step AI code generation process."""
        # Get user's request
        self._start_input("Describe what you want to build", self._do_ai_generation)

    def _do_ai_generation(self, user_request):
        """Execute the two-step AI code generation."""
        if not user_request or not user_request.strip():
            self._set_status("Generation cancelled")
            return

        self._generating = True
        self._gen_status = "Initializing..."
        self.mode = "GENERATING"

        def update_status(msg):
            self._gen_status = msg

        # Run generation in a way that allows cancellation
        try:
            success, result = two_step_code_generation(user_request, update_status)
            
            if success:
                # Create a new prompt with the generated code
                self.prompts.append({
                    "id": str(uuid.uuid4()),
                    "title": user_request[:50] + ("..." if len(user_request) > 50 else ""),
                    "content": result,
                    "generated": True,
                    "timestamp": datetime.now().isoformat(),
                })
                save_prompts(self.prompts)
                self.sel = len(self.prompts) - 1
                self._clamp()
                self._set_status("Code generated and saved!")
            else:
                self._set_status(result, err=True)
        except Exception as e:
            self._set_status(f"Generation error: {e}", err=True)
        finally:
            self._generating = False
            self.mode = "LIST"

    # ── Modal helpers ─────────────────────────────────────────────────────

    def _start_input(self, prompt, callback, initial=""):
        """Switch to INPUT mode and show a centered prompt + text field."""
        self.mode = "INPUT"
        self._input_prompt = prompt
        self._input_buf = initial
        self._input_cb = callback
        curses.curs_set(1)  # show cursor for typing feedback

    def _confirm(self, msg, callback):
        """Switch to CONFIRM mode with a centered yes/no dialog."""
        self.mode = "CONFIRM"
        self._confirm_msg = msg
        self._confirm_cb = callback


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main(stdscr):
    App(stdscr).run()


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Fatal: {e}", file=sys.stderr)
        sys.exit(1)
