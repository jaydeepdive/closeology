# Publish Closeology to GitHub (one 30-second step → then fully automatic)

Your GitHub account is fine. The only thing blocking an automatic push is that
this cloud session (and the desktop Cowork VM) are both fenced off from GitHub
by Anthropic's network proxy, and the repo isn't on the session's allow-list.
From your own terminal there's no such fence:

```bash
cd ~/Downloads && unzip -o closeology_bundle.zip -d closeology && cd closeology
git init && git add -A && git commit -m "Project Closeology: BC + Ontario"
git branch -M main
git remote add origin https://github.com/jaydeepdive/closeology.git
git push -u origin main
```

**After that first push, it runs itself.** `.github/workflows/build.yml` rebuilds
BC + Ontario from public data every morning (and on every push) and commits the
refreshed maps + leads back to the repo — no server, no further steps. Nothing
about your leads is public: the repo is private.

Optional public website: uncomment the `pages:` job in build.yml and set
Settings → Pages → Source: GitHub Actions.
