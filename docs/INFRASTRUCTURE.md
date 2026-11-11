# Infrastructure requirements

**Status: in force since 2026-11-11.** The rework towards it is done: everything below describes how things stand, and is the state to keep. "Keeping it" says what to do with work that would not meet it.

This file is the same, byte for byte, in every repository it applies to: `docs/INFRASTRUCTURE.md`, or `.claude/constitution/65-infrastructure.md` in the Go projects. A change to it is made in all of them at once. It names no host, no address and no project, because some of those repositories are public.

Terms used below:
- **The operators** — the two people who run this infrastructure. **The user** is whichever of them the agent is working with.
- **An infrastructure repository** — one that owns a host's configuration: the host itself, its cluster, its accounts.
- **The controller** — the one machine production playbooks run on.

## Requirements

Set for every project. Translated, with the server's name replaced by "the shared server"; nothing else is changed.

1. Everything is installed through Ansible only. Whatever is not in Ansible is to be removed.
2. Every Ansible playbook runs in a test environment first. A local virtual machine, a remote one (on the shared server), whatever — but it is first debugged locally, thoroughly (and autonomously), and only then rolled out to production. Keep the environments carefully isolated, and watch that explicitly at every change.
3. If it is not clear at first sight why something is here, either it is not needed or it is slop in the infrastructure. Look at it critically and rethink it.
4. Backups: be able to bring a database up from a backup in a test environment with a single command, and check that the service works (starts without errors).
5. Databases must be reachable from outside — for example through an SSH tunnel, the way DBeaver does it.
6. Never log in to the shared server as root — only to a personal account with sudo; service accounts for services, where needed.
7. Lock down pod permissions — in particular, read-only and no privileged.
8. Ansible should run on a server, so that the inventory is not shared around. Either on the shared server under a separate account, or on a separate virtual machine (or both).
9. Add explicit graphs and alerts for every regular job and every daemon.

## Decisions

Made by the operators on top of the requirements. The number in brackets is the requirement a decision reads or extends.

**Scope** (all). The requirements cover every host the operators manage: the shared server, the other servers, the test environments and the controller. On a machine the operators do not own, they cover what the operators install there, not the machine itself.

**The provider's image** (1, 3). A host starts from the image its provider installs. Ansible declares what it adds to that image and what it removes from it, each removal with its reason; what is left is what the host needs.

**Deploying** (1).
- A project deploys through an Ansible role in its own repository, versioned with its code: a change to its configuration lands in the same commit and the same release as the code that needs it.
- The infrastructure repository that owns the host says which version runs in which environment. It pins a release tag, with its digest or checksum, per environment in its inventory, and runs the project's role at that tag.
- The role takes everything about where it runs — host, namespace, addresses, secrets — as inputs. It names neither the host nor the repository that pins it.
- Software the operators run but do not develop — with no repository of theirs, or in a fork they do not change — keeps its role in the infrastructure repository that owns the host.
- A deploy is an operator running a playbook, in test as in production. Nothing deploys on a merge or a tag.

**Versioning** (1, 2).
- A release is a tag in the project's repository, built by its CI. No playbook creates a tag.
- Moving a pin is one command that names the tag. It resolves the digest or checksum itself, writes the pin into one environment's inventory and commits it. Nobody copies a checksum by hand.
- A deploy runs only what is pinned. A playbook never picks a version itself, and never "latest": production gets exactly what was tried in test.
- Promoting to production is the same command against the production inventory, as a commit of its own. Rolling back is reverting the commit that moved the pin.

**Test environments** (2). "Autonomously" means self-contained. A test environment has its own credentials, tokens and databases; it reaches nothing of production, and production reaches nothing of it.
- Test environments are virtual machines on the shared server, under KVM.
- Their certificates come from a local CA of their own. So the path through Let's Encrypt is not tried there: a change to it is read with `--check --diff` before production, and watched after.
- What only runs the test environments — the hypervisor, its network, its storage and the virtual machines — is the one exception to requirement 2: the test environments cannot exist before it, so it goes to production untried. It touches nothing production runs on, except the firewall rules that cut its network off; it is read with `--check --diff` first, and rolled back by removing it.

**Restoring** (4). It applies to every database that has backups, wherever they are kept.

**Database access** (5). Each operator reaches a database only through an SSH tunnel to their own account, in test as in production. No database port is open to the internet.

**Accounts** (6). It applies to every host in scope, not only the shared server. Ansible is no exception: it connects through an account and uses sudo.

**Pods** (7).
- Every namespace the operators deploy into enforces the `restricted` Pod Security Standard.
- Every container runs with `readOnlyRootFilesystem: true` — `restricted` does not require it.
- An exception is only for a component that cannot work otherwise, such as the cluster's own system pods, and its reason is written down beside it.

**The controller** (8).
- One controller serves every infrastructure repository. It is a virtual machine of its own, not the shared server.
- Production inventories and vaults live only on the controller. Both operators run playbooks there, each through their own account, and neither keeps a copy on their own machine.
- Each production inventory stays in its private repository, as the controller's off-host copy and its history. Nobody but the controller clones it.
- A test environment's inventory lives with whoever debugs in it locally.

**Graphs and alerts** (9).
- One metrics store and one Grafana serve everything, as part of the shared server's platform. A project writes its metrics there rather than running a store of its own.
- The graphs are on dashboards of their own, apart from any project's.
- Alerts go to a Telegram bot of their own, read by both operators, and to no project's bot: a project's bot already says enough of its own.

## Keeping it

1. **New and changed work meets every requirement and decision here.** A new service goes in through Ansible and through a test environment first, a new pod is read-only and unprivileged, a new regular job or daemon comes with its graphs and alerts, and no new root access appears. When a task cannot be done to them, stop and ask; never make a silent exception.
2. **An exception is only one this file allows, written down beside what needs it, with its reason.** One with no reason written is a violation.
3. **A violation noticed is named in the reply** — what it is, and which requirement — and fixed as work of its own once the user picks it, through a test environment first, never in passing.
4. **Where this file and another rule of a repository disagree, this file holds.** The other rule is a mistake: name it, and change it with the user's word.
5. **A change to the requirements or the decisions is the operators' to make,** in every repository at once.
