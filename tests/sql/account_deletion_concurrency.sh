#!/usr/bin/env bash
# Requires authorized disposable database prepared by account_deletion.sql.
# Run once: ACCOUNT_DELETION_TEST_DSN=... bash tests/sql/account_deletion_concurrency.sh
set -euo pipefail
: "${ACCOUNT_DELETION_TEST_DSN:?Set the disposable PostgreSQL test database DSN}"
artifacts=$(mktemp -d "${TMPDIR:-/tmp}/account-deletion-concurrency.XXXXXX")

# Both transactions hold the school lock through their commit. Either contender
# may win; exactly one must succeed and the other must retain the final admin.
for user_id in 3 4; do
    psql "$ACCOUNT_DELETION_TEST_DSN" -X -Atq -v ON_ERROR_STOP=1 \
        -c "BEGIN; SET LOCAL lock_timeout='10s'; SELECT public.delete_own_account($user_id,'concurrent-$user_id',4102444800); SELECT pg_sleep(1); COMMIT;" \
        > "$artifacts/$user_id" &
    if [[ $user_id == 3 ]]; then first_pid=$!; else second_pid=$!; fi
done
wait "$first_pid"
wait "$second_pid"
python3 - "$artifacts/3" "$artifacts/4" <<'PY'
import pathlib
import sys

outcomes = sorted(line for path in sys.argv[1:] for line in pathlib.Path(path).read_text().splitlines() if line)
assert outcomes == ["last_school_admin", "ok"], outcomes
print("Concurrent final-admin protection passed:", outcomes)
PY
printf 'Captured PostgreSQL output: %s\n' "$artifacts"
