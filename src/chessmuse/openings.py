"""Small, exact opening recognizer; unknown formations retain normal calls."""

BOOK = (
    (("c3c4", "b7c7", "h2e2", "c9e7"), "仙人指路转中炮，对卒底炮飞象", "红方仙人指路转中炮，黑方卒底炮飞象。"),
    (("h2e2", "h9g7", "h0g2", "b9c7"), "中炮对屏风马", "红方摆上当头炮，黑方架起屏风马。"),
    (("h2e2", "h7e7", "h0g2", "h9g7"), "顺手炮", "双方顺手炮，接着各自跳马出子。"),
    (("h0g2", "h9g7", "b0c2", "b9c7"), "双方起马", "双方先跳双马，展开阵型。"),
)


def recognize_opening(moves: list[str]) -> dict | None:
    for prefix, name, text in BOOK:
        if moves[:len(prefix)] == list(prefix):
            return {"name": name, "text": text, "moves": list(prefix)}
    return None
