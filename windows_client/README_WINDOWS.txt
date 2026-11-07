TELUGU VOICE-OVER: GUIDE FOR WINDOWS
====================================

You drop files into folders and double-click a file. The server makes the audio.
Your PC only sends files and collects the results, so you can close it while a job runs.


FIRST-TIME SETUP (once only)
----------------------------
1. Copy these files to C:\AI VOICE.

2. Check the server login. Open PowerShell and type:

       ssh tts-server whoami

   It must print exactly:   pavan

   - If it prints another name (such as root): your SSH alias uses the wrong account.
     Open C:\Users\<you>\.ssh\config and, under "Host tts-server", set:  User pavan
   - If it says "Permission denied": the server does not know your key yet. Type:

       Get-Content "$HOME\.ssh\id_ed25519.pub"

     (Use your own key's file name if it is different.) Copy the single line it prints.
     This is your PUBLIC key, which is safe to share. Your PRIVATE key (the file without
     .pub) must never leave your PC. On the server, as the user pavan, run:

       /home/pavan/voice/bin/authorize_key.sh "paste the line here"

3. Double-click 3_Check_Status.bat. It creates the folders below. If it lists jobs or says
   "There are no jobs yet", everything is connected.


YOUR FOLDERS
------------
01_voice_clips     Put your recording here
02_scripts_inbox   Put your scripts here
03_output          Finished audio arrives here
04_done            Used files are moved here. Nothing is deleted.
logs               A diary of what happened, one file per day
config.ini         Settings (open with Notepad)


SETTINGS (config.ini)
---------------------
ServerAlias=tts-server   The SSH name of your server
Voice=pavan              Which voice to use
TestOnly=true            true = make only the first 3 sections, to check them first
PollSeconds=20           How often to ask the server for progress


A. ADD YOUR VOICE (once per voice)
----------------------------------
1. Record 10 to 15 seconds in your normal teaching voice, in a quiet room.
2. Name the file after the voice and put it in 01_voice_clips, for example pavan.m4a.
   Types: mp3, m4a, wav, ogg, opus.
3. Best results: also add a text file with the same name (pavan.txt) holding the EXACT
   words you spoke. In Notepad choose Save As, Encoding: UTF-8.
4. Double-click 1_Add_My_Voice.bat.

If you see a red box saying CHECK THE TRANSCRIPT, the words were written by the computer
and may be wrong. Wrong words are the most common cause of poor voice quality. To fix it,
put the clip back in 01_voice_clips with a corrected .txt and run step 4 again.

Your existing voice "pavan" needs this fix. Its transcript contains guessed words.


B. MAKE A VOICE-OVER
--------------------
1. Put your script in 02_scripts_inbox. Either:
   - a lecture .md or .txt file, where each "### " heading makes one audio file and only the
     [NARRATION] text is spoken, or
   - a folder of slide_01.txt, slide_02.txt ... where each file makes one audio file.
2. Double-click 2_Make_Voiceover.bat ONCE. Leave the window open to see progress, such as
   "Slide 3/7 - generating audio, chunk 12/26, about 20 min left". The time is an estimate.
3. When you hear the sound, your audio is in 03_output\<script name>.

Time needed: about 3 minutes of server time for each minute of audio.
If the window closes or your internet drops, the job keeps going on the server. Double-click
2_Make_Voiceover.bat again and it picks up the same job.


MAKE IT LIVELY: [STYLE] TAGS (optional)
---------------------------------------
The voice copies the mood of the recording it learned from, so a lively voice needs a lively
recording. Without these tags, everything works as before.
1. Record 10 to 15 seconds of real excitement, and separately 10 to 15 seconds of you joking.
   Name them pavan_excited.m4a and pavan_funny.m4a, each with a .txt of the exact words.
2. Put them in 01_voice_clips and run 1_Add_My_Voice.bat.
3. Inside [NARRATION] text, switch mood with a tag. It lasts until the next tag, and each new
   section starts in the normal voice:

       [STYLE excited] ...exciting line...
       [STYLE funny] ...small joke...
       [PAUSE 1s]
       [STYLE normal] ...back to teaching...

Use it for a line or two at a time. Very short lines take longer to generate per second of
audio. Live_Speak ignores these tags. Always listen to the result.


C. APPROVE THE FULL BATCH
-------------------------
With TestOnly=true you first get only the first 3 sections.
1. Listen to them in 03_output\<script name>.
2. If they sound right, double-click 4_Full_Batch_Approved.bat. It makes the whole script.

The test files are kept in a _previous_... folder next to the full audio.


OTHER BUTTONS
-------------
3_Check_Status.bat   Shows running and recent jobs.
5_Download_All.bat   Copies the files of every finished job to 03_output\all_jobs, one folder per
                     job, and checks every file's size. Nothing on the server is deleted. Safe to
                     repeat: completed jobs are skipped.
Watch_Mode.bat       Keep this open, and anything dropped in 02_scripts_inbox starts by itself
                     about 10 seconds after the copy finishes. Close the window to stop.
Live_Speak.bat       Quick single text. Write Telugu in C:\AI VOICE\live_text.txt (saved as
                     UTF-8), or drop a .txt file onto this button. The MP3 arrives in
                     03_output\live. A short sentence takes about 15 to 30 seconds.


FILES YOU GET
-------------
Voice_001.wav / .mp3   The audio, one per section, in order
manifest.csv           Which file is which section, with lengths
qc_report.csv          The computer's listening check. Use it to find chunks worth a listen.
                       It does not replace listening, because Telugu recognition is imperfect.
unknown_terms.txt      English words the system guessed how to say. Tell the server admin
                       about any that sound wrong, so they can go in lexicon.tsv.


IF SOMETHING GOES WRONG
-----------------------
"Another window of this tool is already running"
    Only one window can work at a time. Use the other window, or close it and run this again.
"Cannot connect to the server"
    Check your internet and that the server is powered on in the dashboard, then try again.
"logs in as ..., but it must log in as 'pavan'"
    See First-time setup, step 2.
"Voice ... not found"
    Add it (part A), or fix the Voice= line in config.ini.
"The script has no '### ' sections with [NARRATION]"
    Check that the script has "### " headings and [NARRATION]: ... [DURATION]: lines.
Telugu shows as boxes in the black window
    That is only the window's font. Your files and audio are fine.
A job failed
    The red message says why, followed by the last lines of the server log. Your input stays
    in the inbox. Fix the problem and run it again; it continues where it stopped.

REMEMBER: the server bills while it is powered on. Power it off from the E2E Networks
dashboard when you finish, and check there that billing has stopped.
