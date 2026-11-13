# ralph

`ralph.sh` repeats one request until it is done. The request does not change. Progress is whatever the last round left on disk.

```bash
./ralph.sh --check "python3 check.py" "the request"
./ralph.sh -n 20 --check "node check.js" "the request"   # optional fuse
```

Done means the check exits 0 at the start of a round. With no `--check`, done means `team.py do` exits 0, which proves the files run. The header of `ralph.sh` is the short spec. The chat harness calls this from `build`; see `HARNESS.md`.

A dataset, where one function is called on many inputs and the hard part is the collection, is blossom. Read `BLOSSOM.md` before writing the check or starting the loop. The check is the spec. Sizes are floors. The values are written by the model.
