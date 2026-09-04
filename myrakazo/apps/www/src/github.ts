import { GITHUB_API_REPO } from "./site";

export async function fetchGithubStars(): Promise<number | null> {
  try {
    const response = await fetch(GITHUB_API_REPO, {
      headers: {
        Accept: "application/vnd.github+json",
        "User-Agent": "rakazo-www",
      },
    });
    if (!response.ok) {
      return null;
    }
    const payload = (await response.json()) as { stargazers_count?: unknown };
    return typeof payload.stargazers_count === "number" ? payload.stargazers_count : null;
  } catch {
    return null;
  }
}

export function formatStarCount(count: number): string {
  if (count < 1000) {
    return String(count);
  }
  const thousands = count / 1000;
  const rounded = thousands >= 10 ? Math.round(thousands) : Math.round(thousands * 10) / 10;
  return `${rounded}k`.replace(/\.0k$/, "k");
}
