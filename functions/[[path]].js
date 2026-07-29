// Cloudflare Pages Function - intercepts ALL requests to the site.
// Access is granted only if filename matches, token matches, AND user-agent
// looks like a podcast client. Any other request gets a 404.
// This makes the site look completely empty to random visitors.

const FEED_FILENAME = "pkfsfm9xqbhxbxkc.xml";
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

  // Wrong path
  if (path !== FEED_FILENAME) {
    return new Response("Not Found", { status: 404 });
  }

  // Wrong token
  const token = url.searchParams.get("k");
  if (token !== EXPECTED_TOKEN) {
    return new Response("Not Found", { status: 404 });
  }

  // Not a podcast client
  const ua = request.headers.get("user-agent") || "";
  if (!isPodcastClient(ua)) {
    return new Response("Not Found", { status: 404 });
  }

  // All checks passed - serve the static file
  return next();
}
