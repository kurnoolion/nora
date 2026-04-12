# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This repository contains Verizon (VZW) Open Alliance (OA) specification documents for February 2026. It is a document-only repository with no source code.

## Contents

The PDFs cover LTE feature specifications:

- `LTESMS.pdf` — LTE SMS specification
- `LTEAT.pdf` — LTE AT commands specification
- `LTEB13NAC.pdf` — LTE Band 13 NAC specification
- `LTEDATARETRY.pdf` — LTE data retry specification
- `LTEOTADM.pdf` — LTE Over-The-Air Device Management (OTA DM) specification

## Project Context

We are building an AI system (Knowledge Graph + RAG) for intelligent querying, cross-referencing, and compliance analysis of MNO device requirement specifications. The full technical design is in `TDD_Telecom_Requirements_AI_System.md`.

Key architectural decisions and rationale are captured in project memory (`design_decisions.md`). The VZW document structure analysis (critical for parser design) is in `vzw_document_structure.md`.

Current phase: PoC development — starting with PDF extraction and structural parsing.
