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
