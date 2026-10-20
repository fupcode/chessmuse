// ChessMuse's bounded search harness for ElephantEye (LGPL-2.1-or-later).
// Upstream modules remain unmodified and are linked at build time.
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>
#include "position.h"

struct Result { int value; std::vector<int> pv; };
static long long nodes = 0;
static bool traditional = false;
static const int values[7] = {0, 120, 120, 270, 600, 285, 30};

static int material(const PositionStruct &p) {
    int total = 0;
    for (int pc = 16; pc < 48; ++pc) {
        if (!p.ucsqPieces[pc]) continue;
        total += (pc < 32 ? 1 : -1) * values[PIECE_TYPE(pc)];
    }
    return p.sdPlayer == 0 ? total : -total;
}

static std::string iccs(int mv) {
    uint32_t packed = MOVE_COORD(mv);
    return std::string(reinterpret_cast<char*>(&packed), 4);
}

static std::vector<int> ordered(const PositionStruct &p) {
    MoveStruct generated[MAX_GEN_MOVES];
    int count = p.GenAllMoves(generated);
    std::vector<int> moves;
    for (int i=0; i<count; ++i) moves.push_back(generated[i].wmv);
    std::stable_sort(moves.begin(), moves.end(), [&](int a, int b) {
        int va = p.ucpcSquares[DST(a)] ? values[PIECE_TYPE(p.ucpcSquares[DST(a)])] : 0;
        int vb = p.ucpcSquares[DST(b)] ? values[PIECE_TYPE(p.ucpcSquares[DST(b)])] : 0;
        return va == vb ? iccs(a) < iccs(b) : va > vb;
    });
    return moves;
}

static Result search(PositionStruct &p, int depth, int alpha, int beta) {
    ++nodes;
    if (p.IsMate()) return {-MATE_VALUE + p.nDistance, {}};
    if (depth == 0) return {traditional ? p.Evaluate(-MATE_VALUE, MATE_VALUE) : material(p), {}};
    Result best{-MATE_VALUE, {}};
    for (int mv : ordered(p)) {
        if (!p.MakeMove(mv)) continue;
        Result child = search(p, depth-1, -beta, -alpha);
        p.UndoMakeMove();
        int value = -child.value;
        if (value > best.value) {
            best = {value, {mv}};
            best.pv.insert(best.pv.end(), child.pv.begin(), child.pv.end());
        }
        alpha = std::max(alpha, value);
        if (alpha >= beta) break;
    }
    return best;
}

int main(int argc, char **argv) {
    if (argc != 4) return 2;
    int depth = std::atoi(argv[2]);
    if (depth < 1 || depth > 5) return 2;
    traditional = std::string(argv[3]) == "traditional";
    PreGenInit();
    PreEval.bPromotion = false;
    PositionStruct p;
    p.FromFen(argv[1]);
    p.PreEvaluate();
    struct Candidate { int move; Result result; };
    std::vector<Candidate> candidates;
    for (int mv : ordered(p)) {
        if (!p.MakeMove(mv)) continue;
        // Every root receives a full window: these are same-depth exact ranks,
        // not the bounded scores of discarded alpha-beta root siblings.
        Result child = search(p, depth-1, -MATE_VALUE, MATE_VALUE);
        p.UndoMakeMove();
        Result root{-child.value, {mv}};
        root.pv.insert(root.pv.end(), child.pv.begin(), child.pv.end());
        candidates.push_back({mv, root});
    }
    std::stable_sort(candidates.begin(), candidates.end(), [](const Candidate &a, const Candidate &b) {
        return a.result.value == b.result.value ? iccs(a.move) < iccs(b.move) : a.result.value > b.result.value;
    });
    std::cout << "{\"schema_version\":1,\"nodes\":" << nodes << ",\"candidates\":[";
    bool first = true;
    for (const Candidate &c : candidates) {
        if (!first) std::cout << ',';
        first = false;
        std::cout << "{\"move\":\"" << iccs(c.move) << "\",\"score\":" << c.result.value << ",\"pv\":[";
        for (size_t i=0; i<c.result.pv.size(); ++i) {
            if (i) std::cout << ',';
            std::cout << '\"' << iccs(c.result.pv[i]) << '\"';
        }
        std::cout << "]}";
    }
    std::cout << "]}\n";
}
