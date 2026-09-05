# Human-like shogi training data (Shogi Wars)

Generated from ~12k crawled Shogi Wars KIF games with
`human/kif_to_csa.py` + `human/csa_to_hcpe_by_rating.py` (see the repo's
`human/` directory).

Each position is bucketed by the **dan/kyu rank of the player to move**; the
training target is the move that player actually chose (imitation / Maia-style).
Format: `cshogi.HuffmanCodedPosAndEval` records (`hcp`, `bestMove16`, `eval=0`,
`gameResult`). Train/test split is per game.

| Band       | Ranks            | train    | test   |
|------------|------------------|----------|--------|
| `kyu`      | 30級 – 1級        | 269,419  | 6,506  |
| `dan1-3`   | 初段 – 三段        | 186,802  | 3,425  |
| `dan4-6`   | 四段 – 六段        | 518,150  | 12,302 |
| `dan7plus` | 七段 and above     | 123,078  | 1,903  |

Total: 1,121,585 positions.

## Train (per band)

For human imitation the **policy** is what matters, so use outcome-only value
(`--val_lambda 1.0`) and read the logged `test accuracy` (first number = policy
move-match rate) as the metric, not playing strength:

```bash
python -m pydlshogi2.train kyu/train.hcpe kyu/test.hcpe \
    --gpu 0 --amp --epoch 10 --val_lambda 1.0 \
    --checkpoint model-kyu-{epoch:03}.pth
```
