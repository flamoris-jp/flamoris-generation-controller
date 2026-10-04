# FLAMORIS Repository Template

Standard repository template for FLAMORIS projects.

Use this repository as the starting point for new FLAMORIS repositories. After creating a repository from this template, replace the placeholders in this README with project-specific information and add only the language, runtime, build, and deployment files the project actually needs.

## Project

**Name:** `<PROJECT_NAME>`

**Description:** `<PROJECT_DESCRIPTION>`

**Status:** `<planned | development | stable | meta>`

For repositories in `flamoris-jp`, keep this wording aligned with the organization `development_status` custom property. State implemented behavior separately from planned work. Do not leave a repository looking like a future design after its runtime or product slice has already shipped.

## 🧭 Repository identity / このRepositoryは何者？

Replace the placeholders below with short, project-specific statements. Keep them near the top so a human or AI assistant can understand the repository before digging through Issues or source code.

### What it is / 何者か

`<ONE_OR_TWO_SENTENCE_PROJECT_IDENTITY>`

### What it owns / 主な責任範囲

- `<PRIMARY_RESPONSIBILITY_OR_AUTHORITY>`
- `<SECONDARY_RESPONSIBILITY_IF_NEEDED>`

### What it does not own / 持たない責任

- `<IMPORTANT_NEIGHBORING_RESPONSIBILITY_OWNED_ELSEWHERE>`

Delete this subsection only when there is genuinely no likely ownership confusion.

### Current status / 現在の状態

`<WHAT_IS_IMPLEMENTED_NOW_AND_WHAT_IS_STILL_PLANNED_OR_UNACCEPTED>`

Do not describe planned behavior as shipped, or implemented behavior as merely future design.

### Where it fits / FLAMORISのどこに属する？

Start from the [FLAMORIS organization map](https://github.com/flamoris-jp/.github).

When relevant, also link the appropriate family map:

- 🎨 Windows / Desktop: [FLAMORIS Desktop Ecosystem](https://github.com/flamoris-jp/flamoris-commons/blob/main/docs/desktop-ecosystem.md)
- 🤖 AI / MCP services: [FLAMORIS AI Ecosystem](https://github.com/flamoris-jp/flamoris-ai/blob/main/docs/ai-ecosystem.md)

The shared repository documentation policy lives in [FLAMORIS Commons](https://github.com/flamoris-jp/flamoris-commons/blob/main/docs/repository-policy.md).

## 🏷️ GitHub metadata checklist / GitHub表示設定

After creating a repository from this template, configure the GitHub repository metadata as well as the files.

- **Description:** one concise sentence describing the repository's current role.
- **Topics:** include `flamoris`, then add a small set of useful project/domain/technology topics. Prefer roughly 4–7 intentional topics over filling every slot.
- **Development status:** set the organization `development_status` custom property and keep it aligned with this README.
- **Visibility:** choose intentionally; do not expose deployment secrets, private topology, credentials, or private assets by making a repository public.

Do not use obsolete or speculative Topics to advertise responsibilities the repository does not actually own.

> GitHub repository metadata is not repository file content. When creating from a template, verify these settings explicitly rather than assuming every template setting was inherited. 🐾

## Getting started

Document the real setup, build, test, and run commands for this repository here.

Do not copy commands from another FLAMORIS project unless they have been verified against the current implementation.

## Repository principles

- Keep the repository focused on one clear responsibility.
- Treat current code, tests, documentation, and repository configuration as the source of truth.
- Keep public documentation portable: describe product/runtime contracts without publishing private hostnames, credentials, deployment topology, or machine-specific paths.
- Prefer explicit boundaries over speculative abstractions.
- Keep secrets, credentials, tokens, and private data out of source control and logs.
- Add tests where practical and document externally visible behavior.
- Inspect existing FLAMORIS shared packages before introducing duplicate infrastructure.
- AI-assisted development is welcome; submitted changes still require human review and responsibility.

## FLAMORIS

FLAMORIS is open-source software for creative work and AI-native production.

Use it however you like.

Commercial use is welcome and does not require permission. If you'd like, we'd be happy to hear what you used FLAMORIS for. This is completely optional.

FLAMORIS software is provided as-is. We do not provide individual support or guaranteed assistance.

If you run into trouble, let your AI assistant read the repository, documentation, Issues, tests, logs, and source code and help you solve it.

If FLAMORIS helps you or you find it interesting, your support helps fund development and keeps the project growing. 🌱

<sub>Mostly GPU bills.</sub>

---

## FLAMORISについて

FLAMORISは、クリエイティブ制作とAIネイティブな制作環境のためのオープンソースソフトウェアです。

勝手に使ってください。改造しても、組み込んでも、面白いものや変なものを作ってもOKです。

商用作品や製品で使う場合も許可は不要です。もしよければ「こんなのに使ったよ」と教えてもらえるとうれしいです。もちろん強制ではありません。

FLAMORISのソフトウェアは現状のまま提供されます。個別サポートや動作保証はありません。

困ったときは、README、ドキュメント、Issue、テスト、ログ、ソースコードをあなたのAIに読ませて、自己サポートしてもらってください。

もしお役に立てたり、面白いと思っていただけたなら、開発費用をご支援いただけるとうれしいです。FLAMORISは元気になって育ちます。🌱

<sub>主にGPU代とか。</sub>

## License

Code in this repository is licensed under the [Apache License 2.0](LICENSE), unless otherwise noted.

AI models, model weights, datasets, media, and other non-code assets may use separate licenses. State their applicable licenses alongside those assets.
