# TYSA Score — Route to Reference Manifest

This manifest records all 40 UI reference screens, their implemented URL routes, purpose, and visual verification artifacts.

| # | Reference Image | URL Route | Screen Purpose & Module | Captured Verification Artifact |
|---|---|---|---|---|
| 01 | `01_Login.png` | `/accounts/login/` | Staff and judge authentication; benefits, yoga branding on left | `docs/screenshots/01_Login.png` |
| 02 | `02_Admin_Dashboard.png` | `/dashboard/` | 6 colored module sections, live stats, search, quick links | `docs/screenshots/02_Admin_Dashboard.png` |
| 03 | `03_Championship_Setup.png` | `/championships/<id>/setup/` | Independent event-wise category & gender division toggles | `docs/screenshots/03_Championship_Setup.png` |
| 04 | `04_Users_and_Permissions.png` | `/accounts/users/` | Staff directory, scoped roles, module access, assignment drawer | `docs/screenshots/04_Users_and_Permissions.png` |
| 05 | `05_Athlete_Personal_Details.png` | `/register/athlete/step-1/` | Step 1 of public/key registration: personal data, photo, district | `docs/screenshots/05_Athlete_Personal_Details.png` |
| 06 | `06_Event_and_Pair_Selection.png` | `/register/athlete/step-2/` | Step 2: 4 Event cards, derived category, pair partner lookup | `docs/screenshots/06_Event_and_Pair_Selection.png` |
| 07 | `07_Unrestricted_Asana_Selection.png` | `/register/athlete/step-3/` | Step 3: Published catalogue on left, ordered sequence on right | `docs/screenshots/07_Unrestricted_Asana_Selection.png` |
| 08 | `08_Athlete_Status_and_ID_Card.png` | `/athletes/id-card/<id>/` | Permanent public ID, private key warning/copy, printable ID card | `docs/screenshots/08_Athlete_Status_and_ID_Card.png` |
| 09 | `09_Universal_Judge_Registration.png` | `/judges/apply/` | Universal judge application (certification level, experience) | `docs/screenshots/09_Universal_Judge_Registration.png` |
| 10 | `10_Judge_Profile_and_Assigned_Panels.png` | `/judges/<id>/` | Universal judge profile, Admin approval status, scheduled panels | `docs/screenshots/10_Judge_Profile_and_Assigned_Panels.png` |
| 11 | `11_Admin_Volunteer_Guest_COACH_Registers.JPG` | `/registers/` | Admin-entered Coach, Volunteer, Guest registers with Add drawer | `docs/screenshots/11_Admin_Volunteer_Guest_COACH_Registers.png` |
| 12 | `12_District_Athlete_Approval.png` | `/district/approvals/` | District quota tracker, applicant queue, documents, approve/reject | `docs/screenshots/12_District_Athlete_Approval.png` |
| 13 | `13_Venue_Verification.png` | `/venue/verification/` | Search by name OR district OR mobile, match list, clearance checklist | `docs/screenshots/13_Venue_Verification.png` |
| 14 | `14_District_Draw_and_Master_Roll.png` | `/competition/draw/` | District lottery, round-robin master roll generator, lock roll | `docs/screenshots/14_District_Draw_and_Master_Roll.png` |
| 15 | `15_CD_Judge_Panel_Assignment.png` | `/competition/panels/` | CD panel assignment matrix (D1-D4, Chief, Time, Evaluator) | `docs/screenshots/15_CD_Judge_Panel_Assignment.png` |
| 16 | `16. stage manager.jpg` | `/competition/stage/` | Stage queue, batch push controls, submission monitor, 2nd screen | `docs/screenshots/16_Stage_Manager.png` |
| 16.1 | `16.1 second screen.jpg` | `/display/second-screen/` | High-contrast auditorium display, current batch standings, sync | `docs/screenshots/16.1_Second_Screen.png` |
| 17 | `17_Traditional_D_Judge.png` | `/scoring/traditional-d/` | 1-5 athlete cards, posture/stability inputs, read-only time, 7 asanas | `docs/screenshots/17_Traditional_D_Judge.png` |
| 18 | `18_Artistic_Single_D_Judge.png` | `/scoring/artistic-single-d/` | 10 execution rows, pose image, BV, evaluator state, effective score | `docs/screenshots/18_Artistic_Single_D_Judge.png` |
| 19 | `19_Artistic_Pair_D_Judge.png` | `/scoring/artistic-pair-d/` | Minimal pair desk: Chest A, Chest B, formation execution inputs | `docs/screenshots/19_Artistic_Pair_D_Judge.png` |
| 20 | `20_Rhythmic_Pair_D_Judge.png` | `/scoring/rhythmic-pair-d/` | Synchronized pair scoring, shared BV, average execution, 10 items | `docs/screenshots/20_Rhythmic_Pair_D_Judge.png` |
| 21 | `21_Music_Event_Time_Judge.png` | `/scoring/music-time/` | Total routine timer + 10 individual hold duration timers | `docs/screenshots/21_Music_Event_Time_Judge.png` |
| 22 | `22_Artistry_Judge.png` | `/scoring/artistry/` | Performance-level artistry criteria (20-pt breakdown), deductions | `docs/screenshots/22_Artistry_Judge.png` |
| 23 | `23_Evaluator_Valid_Invalid.png` | `/scoring/evaluator/` | Declared vs observed sequence, Valid/Invalid toggle, reason code | `docs/screenshots/23_Evaluator_Valid_Invalid.png` |
| 24 | `24_Chief_Judge_Review.png` | `/scoring/chief-judge/` | Realtime judge completeness, component breakdown, provisional release | `docs/screenshots/24_Chief_Judge_Review.png` |
| 25 | `25 jury.png` | `/jury/` | Read-only score audit by chest/name/district, per-judge marks | `docs/screenshots/25_Jury.png` |
| 26 | `26_CD_Correction_and_Finalisation.png` | `/cd/corrections/` | Search chest, inspect marks, reopen 1 judge's entire sheet, finalise | `docs/screenshots/26_CD_Correction_and_Finalisation.png` |
| 27 | `27_Public_Live_Score_Display.png` | `/display/live/` | Pinned current performer, pinned top 3, rotating cumulative table | `docs/screenshots/27_Public_Live_Score_Display.png` |
| 28 | `28_Certificates_and_Printing.png` | `/results/scoreboard/` | Additional cumulative scoreboard display mode | `docs/screenshots/28_Certificates_and_Printing.png` |
| 29 | `29_Statistics_and_Headcounts.png` | `/reports/statistics/` | Deduplicated unique athletes, event entries, pairs, district breakdown | `docs/screenshots/29_Statistics_and_Headcounts.png` |
| 30 | `30_Overall_Championship_Trophies.png` | `/reports/trophies/` | District medals tally, configurable points (G:5, S:3, B:2) | `docs/screenshots/30_Overall_Championship_Trophies.png` |
| 31 | `31_Ceremonies_Photos_and_Press.png` | `/reports/ceremonies/` | Opening/closing ceremonies, guest dignitaries, photo gallery, press | `docs/screenshots/31_Ceremonies_Photos_and_Press.png` |
| 32 | `32_Complete_Championship_Report.png` | `/reports/championship/` | Section checklist, preview, generate combined PDF championship report | `docs/screenshots/32_Complete_Championship_Report.png` |
| 33 | `33_Traditional_Time_Judge.png` | `/scoring/traditional-time/` | Batch athlete hold timers (up to 5 earlier / 3 final), 7-item log | `docs/screenshots/33_Traditional_Time_Judge.png` |
| 34 | `34_Championship_Basic_Details.png` | `/championships/create/` | Championship basics, dates, venue, registration period, event rules | `docs/screenshots/34_Championship_Basic_Details.png` |
| 35 | `35_Traditional_Final_Multiple_Athletes.png` | `/scoring/traditional-d-final/` | Traditional Final batch view (maximum 3 simultaneous athletes) | `docs/screenshots/35_Traditional_Final_Multiple_Athletes.png` |
| ASANA | `ASANA.JPG` | `/catalogue/` | 250 poses + 5 base records, category filter, BV filter, pose editor | `docs/screenshots/ASANA.png` |
| CERT_LIB | `certifictae templete.jpg` | `/certificates/templates/` | Certificate template library, upload background, versioning | `docs/screenshots/Certificate_Template_Library.png` |
| CERT_EDIT | `edit templete.jpg` | `/certificates/templates/<id>/edit/` | Dynamic certificate field drag/coordinate canvas editor | `docs/screenshots/Certificate_Canvas_Editor.png` |
| CERT_GEN | `geerate certifictae export.jpg` | `/certificates/generate/` | Recipient selector, criteria filter, combined PDF & ZIP export | `docs/screenshots/Certificate_Generate_Export.png` |
