// Cloudflare Pages Function - intercepts ALL requests to the site.
//
// Access rules:
//   - cover.jpg is served publicly (no auth). Podcast clients need to
//     fetch the cover image without any secret token.
//   - The feed (pkfsfm9xqbhxbxkc.xml) requires the correct ?k= token
//     AND a User-Agent that looks like a podcast client.
//   - Anything else returns 404 (site looks empty to random visitors).

const FEED_FILENAME = "pkfsfm9xqbhxbxkc.xml";
const IMAGE_FILENAME = "cover.jpg";
const EXPECTED_TOKEN = "ElL7FrjYfFDJ2WI4pKLazx4z2OUO2hTL";

const ALLOWED_UA_SUBSTRINGS = [
  "pocketcasts",
  "pocket casts",
  "itunes",
  "overcast",
  "antennapod",
  "podcast",
  "castbox",
  "podcastaddict",
  "podcast addict",
  "gpodder",
  "downcast",
  "castro",
  "player fm",
  "playerfm",
  "podverse",
  "podfriend",
  "breez",
  "fountain",
];

function isPodcastClient(ua) {
  if (!ua) return false;
  const lower = ua.toLowerCase();
  return ALLOWED_UA_SUBSTRINGS.some(s => lower.includes(s));
}

export async function onRequest(context) {
  const { request, next } = context;
  const url = new URL(request.url);
  const path = url.pathname.replace(/^\/+/, "");

  // Public: cover image (must be reachable without a token so podcast
  // clients can render the podcast artwork).
  if (path === IMAGE_FILENAME) {
    return next();
  }

  // Feed: requires exact filename + token + podcast-client User-Agent.
  if (path !== FEED_FILENAME) {
    return new Response("Not Found", { status: 404 });
  }

  const token = url.searchParams.get("k");
  if (token !== EXPECTED_TOKEN) {
    return new Response("Not Found", { status: 404 });
  }

  const ua = request.headers.get("user-agent") || "";
  if (!isPodcastClient(ua)) {
    return new Response("Not Found", { status: 404 });
  }

  // All checks passed - serve the static file
  return next();
}
