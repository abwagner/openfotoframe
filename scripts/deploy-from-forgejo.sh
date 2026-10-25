#!/usr/bin/env bash
# Installed outside the development checkout; called by a restricted SSH key.
set -euo pipefail
umask 077
export PATH=/usr/local/bin:/usr/bin:/bin
server_dir=/home/awagner/Swagner-Server
repo_dir="$server_dir/openfotoframe"
state_dir=/home/awagner/.local/state/openfotoframe-cd
mkdir -p "$state_dir/releases"
exec 9>"$state_dir/lock"
flock -w 600 9
branch="$(cat "$state_dir/branch")"
git -C "$repo_dir" check-ref-format "refs/heads/$branch" >/dev/null

if [[ "${1:-}" == --pending ]]; then
    [[ -f "$state_dir/pending-sha" ]] || exit 0
    revision="$(cat "$state_dir/pending-sha")"
else
    [[ "${SSH_ORIGINAL_COMMAND:-${1:-}}" =~ ^deploy\ ([0-9a-f]{40})$ ]] || {
        echo 'Expected: deploy <40-character commit SHA>' >&2; exit 2;
    }
    revision="${BASH_REMATCH[1]}"
fi
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || exit 2
git -C "$repo_dir" fetch --no-tags origin "$branch"
latest="$(git -C "$repo_dir" rev-parse FETCH_HEAD)"
if [[ "$revision" != "$latest" ]]; then
    echo "Skipping superseded commit $revision; current $branch is $latest"
    # A newer tested workflow will queue the replacement.
    if [[ -f "$state_dir/pending-sha" && "$(cat "$state_dir/pending-sha")" == "$revision" ]]; then
        rm "$state_dir/pending-sha"
    fi
    exit 0
fi
if [[ -f "$state_dir/deployed-sha" && "$(cat "$state_dir/deployed-sha")" == "$revision" ]]; then
    rm -f "$state_dir/pending-sha"
    echo "Already deployed $revision"; exit 0
fi
# Protect features already running in production until they are merged.
if [[ -f "$state_dir/minimum-sha" ]]; then
    git -C "$repo_dir" merge-base --is-ancestor "$(cat "$state_dir/minimum-sha")" "$revision" || {
        echo 'Refusing to remove unmerged production features; merge the production branch first.' >&2; exit 1;
    }
fi
# Persist before checking schedules, so cron retries deferred deployments.
printf '%s\n' "$revision" > "$state_dir/pending-sha.tmp"
mv "$state_dir/pending-sha.tmp" "$state_dir/pending-sha"
maintenance="$(docker exec openfotoframe curl -fsS http://localhost:5000/api/maintenance-window)"
can_deploy="$(printf '%s' "$maintenance" | python3 -c 'import json,sys; value=json.load(sys.stdin)["can_deploy"]; assert isinstance(value, bool); print(str(value).lower())')"
if [[ "$can_deploy" == false ]]; then
    echo 'Deployment queued until the TV maintenance window; retries every five minutes.'
    exit 0
fi
release_dir="$state_dir/releases/$revision"
mkdir -p "$release_dir"
git -C "$repo_dir" archive "$revision" | tar -x -C "$release_dir"
image="openfotoframe-cd:$revision"
docker build --label "org.opencontainers.image.revision=$revision" -t "$image" "$release_dir"
# Tests run inside an ephemeral container without production volumes or networks.
docker run --rm --network none -v "$release_dir/tests:/app/tests:ro" "$image" python -m pytest -q -p no:cacheprovider /app/tests
# Discard a build if another merge arrived while it was running.
git -C "$repo_dir" fetch --no-tags origin "$branch"
[[ "$(git -C "$repo_dir" rev-parse FETCH_HEAD)" == "$revision" ]] || {
    echo 'A newer commit arrived during the build; skipping this deployment.'; exit 0;
}
previous_image="$(docker inspect openfotoframe --format '{{.Image}}')"
compose=(docker compose -p swagner-home --env-file "$server_dir/.env" -f "$server_dir/stacks/home.compose.yml" -f "$state_dir/image.compose.json")
write_override() {
    python3 - "$1" "$state_dir/image.compose.json" <<'PY'
import json,sys
with open(sys.argv[2], 'w') as f:
    json.dump({'services': {'photo-frame': {'image': sys.argv[1]}}}, f)
PY
}
healthy() {
    for ((attempt=0; attempt<36; attempt++)); do
        [[ "$(docker inspect openfotoframe --format '{{.State.Health.Status}}' 2>/dev/null || true)" == healthy ]] && return 0
        sleep 5
    done
    return 1
}
write_override "$image"
if ! "${compose[@]}" up -d --no-build --no-deps --force-recreate photo-frame || ! healthy; then
    echo 'Deployment failed; restoring the previous image.' >&2
    write_override "$previous_image"
    "${compose[@]}" up -d --no-build --no-deps --force-recreate photo-frame
    healthy || { echo 'Rollback health check failed.' >&2; exit 1; }
    exit 1
fi
printf '%s\n' "$revision" > "$state_dir/deployed-sha.tmp"
mv "$state_dir/deployed-sha.tmp" "$state_dir/deployed-sha"
rm -f "$state_dir/pending-sha"
echo "Deployed $revision; openfotoframe is healthy."
