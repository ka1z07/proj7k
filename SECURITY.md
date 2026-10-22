# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Report it privately through GitHub instead:
[Report a vulnerability](https://github.com/ka1z07/proj7k/security/advisories/new) (the *Security* tab → *Report a
vulnerability*). Chinese or English is fine.

Include what is affected, how to reproduce it, and what an attacker could do with it. You should get a first reply
within a week. Once a fix is released, the advisory is published and you are credited unless you prefer not to be.

## Supported versions

Only the latest release, and `main`, receive fixes.

## What is in scope

proj7k runs on a player's own computer, so the things worth reporting are mostly about what a malicious chart,
replay or web page could make it do:

- The local dashboard (`127.0.0.1:7770`) and live radar: anything another website or another user on the network can
  reach or trigger.
- Parsing of `.osu`, `.osz` and `.osr` files and uploaded audio: crashes are bugs, but code execution, writing files
  outside the expected folders, or reading arbitrary files are security issues.
- The osu!lazer database access (`proj7k.lazer`): anything that could corrupt or leak a player's library or scores.
- The installers (`install.cmd`, `install.sh`, `deploy/`) and the desktop window's update download.
- The public chart catalog site.

Out of scope: problems in osu! itself (report them to ppy), and denial of service by feeding your own machine a huge
file.
