# VRS GTFS: Data Management Group Project

SRH University Heidelberg · Data Management (m_ADSA_010-HD) · Group LearninMachines

## Question
Can the VRS static timetable feed be trusted, reused and fed into an AI model, and how do we prove it?

## Dataset
- Publisher: Verkehrsverbund Rhein-Sieg (VRS), VRS Open Data Service
- Source URL: https://www.vrs.de/en/future-mobility/for-companies/open-data-service
- Licence: Datenlizenz Deutschland – Zero – 2.0 (https://www.govdata.de/dl-de/zero-2-0)
- Downloaded: 2026-11-07 · File: GTFS_VRS_mit_SPNV_1.zip (16.4 MB)
- SHA-256: d0e2f61a1cf644c61a3aa6d3177315a4eae41c4e1ed78b8e6bd2f64f0c44f3b8
- Feed version: VERSION__20250114_2327 · Valid: 2024-08-21 to 2025-03-30
- Known limitation: this feed expired on 2025-03-30. Findings describe the timetable valid 2024-08-21 to 2025-03-30; the method applies unchanged to a current feed.
- Archived copy of the exact file: https://github.com/vivek-talreja/dm-vrs-gtfs/archive/refs/tags/data-2026-11-07.zip

## What is in this repo
| Folder | Content |
|---|---|
| flows/ | Tableau Prep flow (.tflx) |
| scripts/ | Python cleaning functions, pipeline, tests |
| outputs/ | Validator reports (before/after), cleaning log |
| docs/ | Metadata record, data dictionary, FAIR assessment |
| slides/ | Presentation |

## How to reproduce
1. Download the file from the URL above and check the SHA-256.
2. Unzip into data/raw/vrs_gtfs/<date>/.
3. pip install -r requirements.txt
4. python scripts/pipeline.py <args>   (to be filled in once it exists)

## Team
<names and what each person worked on>

## Licence
Code: MIT. Documentation: see LICENSE. Data: dl-de/zero-2.0 (source: VRS).
