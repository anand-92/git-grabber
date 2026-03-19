# Git Grabber

> Copy a public GitHub folder or file into the current working directory without cloning the whole repo.

Git Grabber is a skill that lets you share a GitHub `tree` or `blob` URL and grab just that folder or file into your working directory.

It uses the same general idea as DownGit, but instead of building a zip download, it writes the selected GitHub content directly to disk.

## What it does

- Copies a single public GitHub folder or file from a shared URL
- Works with `tree` URLs, `blob` URLs, and repo root URLs
- Preserves the selected folder name by default
- Avoids silently overwriting existing files unless you explicitly ask for it
- Handles refs safely, including branch or tag names that contain slashes

## Example

If you share:

`https://github.com/leemysw/agent-kit/tree/main/docs/guides`

and say something like:

```text
Grab this folder into the current directory.
```

the skill should create:

```text
./guides/
```

and copy the files from that GitHub folder into it.

## Installation

Install it with:

```bash
npx skills add https://github.com/anand-92/git-grabber.git
```

`npx skills add anand-92/git-grabber` handles the install for you.

## Verify the install

After installation, try a prompt like:

```text
Copy this GitHub folder into the current directory:
https://github.com/leemysw/agent-kit/tree/main/docs/guides
```

## Usage

Use plain language. Good prompts include:

```text
Grab this GitHub folder into the current directory:
https://github.com/leemysw/agent-kit/tree/main/docs/guides
```

```text
Copy just this file from GitHub:
https://github.com/user/repo/blob/main/path/to/file.ts
```

```text
Copy this folder into ./vendor and overwrite the existing copy:
https://github.com/user/repo/tree/main/src/shared
```

## Supported URLs

- `https://github.com/<owner>/<repo>`
- `https://github.com/<owner>/<repo>/tree/<ref>/<path>`
- `https://github.com/<owner>/<repo>/blob/<ref>/<path>`

## Notes

- Public GitHub repositories only
- Existing local targets are not overwritten unless you explicitly ask
- `GITHUB_TOKEN` or `GH_TOKEN` can be used to reduce GitHub API rate-limit issues
- Best fit for “grab this folder/file” requests where cloning the whole repo would be overkill

## License

MIT
