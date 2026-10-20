"""Small boundary around the pinned rules library, using ICCS coordinates."""

import re

from cchess import ChessBoard


def play(fen: str, move: str) -> tuple[str, str]:
    """Return next FEN and Chinese notation; reject self-check as well as bad moves.

    cchess 1.25.5's move() does not reject self-check or advance the turn.
    These checks must stay here rather than in the player.
    """
    if not re.fullmatch(r"[a-i][0-9][a-i][0-9]", move):
        raise ValueError(f"Invalid ICCS move: {move!r}")
    board = ChessBoard(fen)
    start = (ord(move[0]) - ord("a"), int(move[1]))
    end = (ord(move[2]) - ord("a"), int(move[3]))
    if not board.is_valid_move(start, end) or board.is_checked_move(start, end):
        raise ValueError(f"Illegal move {move} in {fen}")
    result = board.move(start, end)
    notation = result.to_text()
    board.next_turn()
    return board.to_fen(), notation


def move_facts(fen: str, move: str, notation: str) -> dict:
    """Facts for the language model; no generated narration or tactical guesses."""
    board = ChessBoard(fen)
    start = (ord(move[0]) - ord("a"), int(move[1]))
    end = (ord(move[2]) - ord("a"), int(move[3]))
    pieces = {"r": "车", "n": "马", "b": "象", "a": "士", "k": "将", "c": "炮", "p": "卒"}
    moving = board.get_fench(start)
    captured = board.get_fench(end)
    labels = []
    if fen.split()[0] == "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR" and move in {"h2e2", "b2e2"}:
        labels.append("当头炮")
    was_screen = board.get_fench((2, 7)) == "n" and board.get_fench((6, 7)) == "n"
    gives_check = board.is_checking_move(start, end)
    board.move(start, end)
    if moving == "n" and not was_screen and board.get_fench((2, 7)) == "n" and board.get_fench((6, 7)) == "n":
        labels.append("屏风马")
    return {
        "fen": fen, "move": move,
        "side": "红方" if fen.split()[1] == "w" else "黑方",
        "action": notation.translate(str.maketrans("123456789１２３４５６７８９", "一二三四五六七八九一二三四五六七八九")),
        "piece": {"P": "兵", "B": "相", "A": "仕", "K": "帅"}.get(moving, pieces[moving.lower()]),
        "capture": {"P": "兵", "B": "相", "A": "仕", "K": "帅"}.get(captured, pieces[captured.lower()]) if captured else None,
        "gives_check": gives_check, "verified_labels": labels,
    }
