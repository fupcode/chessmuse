"""Validated visual attention for a single spoken beat."""

import re

from cchess import ChessBoard

from chessmuse.game import play


def validate_annotation(annotation: dict | None, fen: str):
    if annotation is None:
        return
    if (not isinstance(annotation, dict)
            or set(annotation) != {"id", "label", "fen", "rings", "arrows"}
            or not isinstance(annotation["id"], str)
            or not isinstance(annotation["label"], str)
            or annotation["fen"] != fen):
        raise ValueError("标注与当前局面不一致")
    rings, arrows = annotation["rings"], annotation["arrows"]
    if (not isinstance(rings, list) or not isinstance(arrows, list)
            or len(rings) > 3 or len(arrows) > 1 or not (rings or arrows)):
        raise ValueError("每段最多一组标注，只强调一个关系")
    board = ChessBoard(fen)

    def piece(square):
        if not isinstance(square, str) or not re.fullmatch(r"[a-i][0-9]", square):
            raise ValueError("标注坐标无效")
        return board.get_fench((ord(square[0]) - 97, int(square[1])))

    for square in rings:
        if not piece(square):
            raise ValueError("标注不能指向空格")
    for arrow in arrows:
        if not isinstance(arrow, dict) or set(arrow) != {"from", "to", "kind"}:
            raise ValueError("标注箭头格式无效")
        attacker, target = piece(arrow["from"]), piece(arrow["to"])
        if not attacker or arrow["kind"] not in {"move", "attack"}:
            raise ValueError("标注箭头缺少行棋子或已核验关系")
        if arrow["kind"] == "attack" and (not target or target.isupper() == attacker.isupper()):
            raise ValueError("攻击标注必须指向对方棋子")
        # This establishes a legal geometric option, not a forced reply.
        hypothetical = fen.split()[0] + (" w" if attacker.isupper() else " b")
        play(hypothetical, arrow["from"] + arrow["to"])
