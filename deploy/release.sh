#!/usr/bin/env bash
# What release.yml runs on a version tag. It builds nothing and deploys nothing: it
# gives the image image.yml built for the tagged commit the version's tag, by digest,
# and publishes the role as an archive with its sum. Nothing is tagged or published
# unless the commit is on master and its image exists.
set -euo pipefail

archive=itgraph-ansible.tar.gz
image=${RELEASE_IMAGE:-ghcr.io/vyhuholl/it-media-graph}
# About a minute is what an image takes to build. A tag pushed right behind its
# commit waits for it, rather than failing and spending a version.
wait_tries=${RELEASE_WAIT_TRIES:-60}
wait_seconds=${RELEASE_WAIT_SECONDS:-10}

fail() {
	printf 'release: %s\n' "$*" >&2
	exit 1
}

[ "$#" -eq 1 ] || fail 'usage: release.sh <tag>'
tag=$1
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "'$tag' is not a version tag vX.Y.Z"

# The tag may be annotated; the release takes the commit it points at.
sha="$(git rev-parse "$tag^{commit}")"
git merge-base --is-ancestor "$sha" origin/master || fail "$tag is on a commit not on master: $sha"

digest_of() { # <image reference>
	docker buildx imagetools inspect "$1" --format '{{.Manifest.Digest}}'
}

tries=0
until want="$(digest_of "$image:$sha" 2>/dev/null)"; do
	tries=$((tries + 1))
	[ "$tries" -lt "$wait_tries" ] || fail "$sha has no image after waiting: image.yml builds it on master"
	sleep "$wait_seconds"
done

docker buildx imagetools create --tag "$image:$tag" "$image:$sha"
got="$(digest_of "$image:$tag")"
[ "$got" = "$want" ] || fail "$image:$tag has the digest $got, not $want"

# From deploy/ansible, the archive holds itgraph/…; from a commit, its entries carry
# the commit's time, so that one commit packs to the same bytes.
work="$(mktemp -d "${TMPDIR:-/tmp}/itgraph-release.XXXXXX")"
trap 'rm -rf "$work"' EXIT
(cd "$(git rev-parse --show-toplevel)/deploy/ansible" && git archive --format=tar "$sha" itgraph) |
	gzip -n >"$work/$archive"
(
	cd "$work"
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$archive" >SHA256SUMS
	else
		shasum -a 256 "$archive" >SHA256SUMS
	fi
)

# From the repository, not from $work: gh finds the repository through git.
gh release create "$tag" "$work/$archive" "$work/SHA256SUMS" --verify-tag --title "$tag" --notes "itgraph $tag"
printf 'release: %s published, at %s\n' "$tag" "$sha"
