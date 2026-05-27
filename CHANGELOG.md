## [2.0.2](https://github.com/mini-app-polis/transcription-cog/compare/v2.0.1...v2.0.2) (2026-05-27)


### Bug Fixes

* pick up common-python-utils with configurable LLM max_tokens ([372a014](https://github.com/mini-app-polis/transcription-cog/commit/372a0145e55add883b3871427c12f6aec477f541))

## [2.0.1](https://github.com/mini-app-polis/transcription-cog/compare/v2.0.0...v2.0.1) (2026-05-27)


### Bug Fixes

* adding context to eval ([1cf2c27](https://github.com/mini-app-polis/transcription-cog/commit/1cf2c27efae28c36780d7d7eebbfd7ba7b207962))

# [2.0.0](https://github.com/mini-app-polis/transcription-cog/compare/v1.10.4...v2.0.0) (2026-05-20)


* feat!: cut over write path from /v1/wcs/notes to /v1/wcs/sources ([c1239cd](https://github.com/mini-app-polis/transcription-cog/commit/c1239cd0f9d1403dc72a7f8367af6a2a7d062c57))


### Features

* updating prompt and schema based on new design requirements as documented ([0734329](https://github.com/mini-app-polis/transcription-cog/commit/07343292ebcb0a904902454e13e54b1d41838dc0))


### BREAKING CHANGES

* The cog no longer writes to the legacy /v1/wcs/notes
endpoint. All extractions now write to /v1/wcs/sources on the new WCS
entity substrate. The legacy notes table is preserved on the API side
as _legacy_wcs_notes for the rebuild window per api-kaianolevine-com
ADR-0004, but the cog stops feeding it.

- Replace NoteCreatePayload / NoteResponse / NotesOutput with
  SourceCreatePayload / SourceResponse in models.py.
- Rename NotesApiClient to SubstrateApiClient; replace create_note()
  with create_source().
- Rewrite flow.py's task_store_notes as task_store_source, building
  the SourceCreatePayload from filename metadata + EXTRACTION_SCHEMA-
  shaped raw_output + extractor/prompt version metadata.
- Replace NOTES_SCHEMA references with EXTRACTION_SCHEMA throughout
  the LLM call path and validation.
- Update flow.py module docstring and step 7 description.
- Tests updated to mock the new endpoint and assert the new payload
  shape including extractor metadata.

Refs: docs/decisions/ADR-005-new-extraction-prompt-and-write-path.md,
      api-kaianolevine-com docs/decisions/ADR-0002-wcs-entity-substrate.md,
      api-kaianolevine-com docs/decisions/ADR-0004-rebuild-wcs-corpus.md
Co-authored-by: Cursor <cursoragent@cursor.com>

## [1.10.4](https://github.com/mini-app-polis/transcription-cog/compare/v1.10.3...v1.10.4) (2026-05-19)


### Bug Fixes

* pipeline eval version reporting ([9a2205d](https://github.com/mini-app-polis/transcription-cog/commit/9a2205d2e390bc4abd6b18fde07f33f3f3f7a5a9))

## [1.10.3](https://github.com/mini-app-polis/transcription-cog/compare/v1.10.2...v1.10.3) (2026-05-17)


### Bug Fixes

* addressing findings ([fd15e7e](https://github.com/mini-app-polis/transcription-cog/commit/fd15e7e1f8ad030da9e4a592137b5f7f44acc5ca))

## [1.10.2](https://github.com/mini-app-polis/transcription-cog/compare/v1.10.1...v1.10.2) (2026-05-17)


### Bug Fixes

* updating cog report name ([4b97b99](https://github.com/mini-app-polis/transcription-cog/commit/4b97b99d12ababf9c14fdd38c23575e5aeee7da5))

## [1.10.1](https://github.com/mini-app-polis/transcription-cog/compare/v1.10.0...v1.10.1) (2026-05-17)


### Bug Fixes

* pipeline findings ([9c7dae3](https://github.com/mini-app-polis/transcription-cog/commit/9c7dae331585ba5b96a28029927d85b73d289781))

# [1.10.0](https://github.com/mini-app-polis/transcription-cog/compare/v1.9.3...v1.10.0) (2026-05-17)


### Bug Fixes

* updated tests and precommit ([23e1805](https://github.com/mini-app-polis/transcription-cog/commit/23e18053217613012ba6ba8b228117e5dfdb5a2b))


### Features

* updated pipeline eval ([3ab87c4](https://github.com/mini-app-polis/transcription-cog/commit/3ab87c4722aeee30996e6bfa076d9c38dea90fbf))

## [1.9.3](https://github.com/mini-app-polis/transcription-cog/compare/v1.9.2...v1.9.3) (2026-05-17)


### Bug Fixes

* escape literal {field, field} examples in system prompt template ([2409c48](https://github.com/mini-app-polis/transcription-cog/commit/2409c48b4f3f09e95932f0fe0f58192247c2f317))
* prompt tighten ([c144681](https://github.com/mini-app-polis/transcription-cog/commit/c1446816d4d3e76ad0c3029deed83912042e4f2f))

## [1.9.2](https://github.com/mini-app-polis/transcription-cog/compare/v1.9.1...v1.9.2) (2026-05-15)


### Bug Fixes

* updated retry timing ([34474ee](https://github.com/mini-app-polis/transcription-cog/commit/34474eef890bdfc5a37cf2490fee665c60e46f58))

## [1.9.1](https://github.com/mini-app-polis/transcription-cog/compare/v1.9.0...v1.9.1) (2026-05-15)


### Bug Fixes

* import path ([44d97ff](https://github.com/mini-app-polis/transcription-cog/commit/44d97ff8b86a986cc9339b230a577537edc1ff69))

# [1.9.0](https://github.com/mini-app-polis/transcription-cog/compare/v1.8.1...v1.9.0) (2026-05-14)


### Bug Fixes

* lock ([6e13fdb](https://github.com/mini-app-polis/transcription-cog/commit/6e13fdb4e887d5739148d056752f0655316ccf74))
* lock ([a37b27a](https://github.com/mini-app-polis/transcription-cog/commit/a37b27a82ee2ba47cb94cfa77c0c30bcecfb1d5f))
* release ([7aa8dbd](https://github.com/mini-app-polis/transcription-cog/commit/7aa8dbdae1633fef0e590ab636f19d79135e6ac8))


### Features

* migrate to transcription cog ([c4a3df1](https://github.com/mini-app-polis/transcription-cog/commit/c4a3df1c2efadae9b81aff18e0236689530eec92))

## [1.8.1](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.8.0...v1.8.1) (2026-05-14)


### Bug Fixes

* build ([d9c800f](https://github.com/mini-app-polis/notes-ingest-cog/commit/d9c800ff79175e977f221b175091e2bbf6d8fd7f))

# [1.8.0](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.7.5...v1.8.0) (2026-05-14)


### Bug Fixes

* build ([f319ba4](https://github.com/mini-app-polis/notes-ingest-cog/commit/f319ba4866c628a7e362b821d3f1b83b024229cc))
* lock ([11a3cd9](https://github.com/mini-app-polis/notes-ingest-cog/commit/11a3cd9638612216dac0d762714eeb73e13ca185))


### Features

* migration of voicenotes into this repo ([d7d3ddb](https://github.com/mini-app-polis/notes-ingest-cog/commit/d7d3ddb13377c3721667a1b760ff809e0935def8))

## [1.7.5](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.7.4...v1.7.5) (2026-05-10)


### Bug Fixes

* **notes-ingest:** add on_failure/on_crashed hook so flow crashes reach the dashboard ([6c3c9f5](https://github.com/mini-app-polis/notes-ingest-cog/commit/6c3c9f57d35460f1d0692cc2d1cf798ca9ed3e6f))

## [1.7.4](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.7.3...v1.7.4) (2026-04-24)


### Bug Fixes

* **drive:** make archive_file idempotent on already-archived input ([86f8d03](https://github.com/mini-app-polis/notes-ingest-cog/commit/86f8d037635e5c27c89af5f360e33d21479705a3))

## [1.7.3](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.7.2...v1.7.3) (2026-04-23)


### Bug Fixes

* **evaluations:** emit one run-level evaluation matching current API schema ([1f87ed9](https://github.com/mini-app-polis/notes-ingest-cog/commit/1f87ed9b617df00f160a5e957bc908cd385367a4))
* **evaluations:** use canonical source="flow_inline" for bucket routing ([3b6368b](https://github.com/mini-app-polis/notes-ingest-cog/commit/3b6368b79bf57b69e42e66d39fe4fc5c7423d8f9))

## [1.7.2](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.7.1...v1.7.2) (2026-04-22)


### Bug Fixes

* **tests:** add mock verification to Drive and evaluation-post tests ([e34d637](https://github.com/mini-app-polis/notes-ingest-cog/commit/e34d637a35fb3531258f51cf80e39e1b4299e002))

## [1.7.1](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.7.0...v1.7.1) (2026-04-20)


### Bug Fixes

* addressing evaluator findings ([4fb0213](https://github.com/mini-app-polis/notes-ingest-cog/commit/4fb0213671a96fa7706bb88d4d9fa68d195c4870))
* addressing evaluator findings ([e2970d8](https://github.com/mini-app-polis/notes-ingest-cog/commit/e2970d8c6b8d9c03f05c8330f476b15316a2e142))

# [1.7.0](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.6.1...v1.7.0) (2026-04-18)


### Features

* bump common-python-utils ([c02e9ca](https://github.com/mini-app-polis/notes-ingest-cog/commit/c02e9ca99d63a0c4dd60d5ed1c2a0f7bcb5c2138))

## [1.6.1](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.6.0...v1.6.1) (2026-04-17)


### Bug Fixes

* bump common-python-utils to 2.4.2 ([3e98a63](https://github.com/mini-app-polis/notes-ingest-cog/commit/3e98a63bc1d745099a1f0c7ab88fdd333c0e68c7))

# [1.6.0](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.5.0...v1.6.0) (2026-04-17)


### Features

* common upgrade ([1c7eb6a](https://github.com/mini-app-polis/notes-ingest-cog/commit/1c7eb6aa28d13e9b571e0625f67805bc4e88c5f7))

# [1.5.0](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.4.0...v1.5.0) (2026-04-17)


### Features

* upgrade common for auth ([93f23b0](https://github.com/mini-app-polis/notes-ingest-cog/commit/93f23b0178961548fdc58414dd3d82ea43f16c74))

# [1.4.0](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.3.2...v1.4.0) (2026-04-17)


### Features

* upgrade common for auth ([1b33c3f](https://github.com/mini-app-polis/notes-ingest-cog/commit/1b33c3f1a1b0f3ca85d345b25b47fc42a26ff8d0))

## [1.3.2](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.3.1...v1.3.2) (2026-04-05)


### Bug Fixes

* concurrency block ([0be733c](https://github.com/mini-app-polis/notes-ingest-cog/commit/0be733c4a468fd7f061da7279d14831245e3dcb3))

## [1.3.1](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.3.0...v1.3.1) (2026-04-05)


### Bug Fixes

* adding known venue names ([d241adc](https://github.com/mini-app-polis/notes-ingest-cog/commit/d241adc4464b1e5a449cde4863387ebc6ad9b9e2))

# [1.3.0](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.2.0...v1.3.0) (2026-04-05)


### Features

* aligning with new ecosystem standards 2.0.0 ([b5fdc66](https://github.com/mini-app-polis/notes-ingest-cog/commit/b5fdc664c1850f7e3efaae9b18687da1c971ca57))

# [1.2.0](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.1.1...v1.2.0) (2026-04-05)


### Features

* updating with filename intake requirements and adding sentry support ([9a0bee9](https://github.com/mini-app-polis/notes-ingest-cog/commit/9a0bee9db2a75657b243e4f0756cef67ca663154))

## [1.1.1](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.1.0...v1.1.1) (2026-04-03)


### Bug Fixes

* skip already-processed transcripts via drive_file_id unique constraint ([82eb64f](https://github.com/mini-app-polis/notes-ingest-cog/commit/82eb64fd1a45e0fe6ee26699327f3eacd1341e26))

# [1.1.0](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.0.4...v1.1.0) (2026-04-03)


### Features

* add concurrency limit to prevent parallel folder scans ([5087a98](https://github.com/mini-app-polis/notes-ingest-cog/commit/5087a98b57820d00f2577a24629a68cbf9c8774c))

## [1.0.4](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.0.3...v1.0.4) (2026-04-03)


### Bug Fixes

* dependency install ([a5ce60f](https://github.com/mini-app-polis/notes-ingest-cog/commit/a5ce60f35cfa9dfe2b4b09eae687fd7672294349))
* uv lock ([e404f7a](https://github.com/mini-app-polis/notes-ingest-cog/commit/e404f7a61f1df29de982e29426f4220b2b43cf94))

## [1.0.3](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.0.2...v1.0.3) (2026-04-03)


### Bug Fixes

* handle transcript data ([db1a9a7](https://github.com/mini-app-polis/notes-ingest-cog/commit/db1a9a75081092566c8f19cd356588a105a8da94))

## [1.0.2](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.0.1...v1.0.2) (2026-04-03)


### Bug Fixes

* in folder file detection ([d220db3](https://github.com/mini-app-polis/notes-ingest-cog/commit/d220db31218e1c1d52fe034a652feadd50cf82c4))

## [1.0.1](https://github.com/mini-app-polis/notes-ingest-cog/compare/v1.0.0...v1.0.1) (2026-04-03)


### Bug Fixes

* flow entry no parameters ([a199e59](https://github.com/mini-app-polis/notes-ingest-cog/commit/a199e5987d8833072c68d13e645aeda5b53eab0b))

# 1.0.0 (2026-04-03)


### Bug Fixes

* tests ([bfe4212](https://github.com/mini-app-polis/notes-ingest-cog/commit/bfe42124bef57e5f2ef630b4ec6b28cbad251601))
* tests update and formatting ([6b0d305](https://github.com/mini-app-polis/notes-ingest-cog/commit/6b0d3051f8c492949b3c04ff55b5a48f62bff4c7))


### Features

* Inital commit ([21cc42d](https://github.com/mini-app-polis/notes-ingest-cog/commit/21cc42de62904a6d275b7af9fc940f7e28219bf4))

# Changelog
