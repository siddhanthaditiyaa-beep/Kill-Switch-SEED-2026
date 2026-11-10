# Push this project to GitHub

1. On github.com click **New repository**, name it `killswitch-gateway`, leave it EMPTY
   (no README, no .gitignore, no licence), click **Create repository**.
2. Unzip this folder on your PC. Open a terminal INSIDE the `killswitch-gateway` folder
   (Windows: open the folder in File Explorer, click the address bar, type `cmd`, press Enter).
3. Paste these one at a time into that terminal:

       git init
       git add .
       git commit -m "KillSwitch Gateway: initial commit"
       git branch -M main
       git remote add origin https://github.com/siddhanthaditiyaa-beep/killswitch-gateway.git
       git push -u origin main

   If git asks you to log in, sign in with your GitHub account in the browser window it opens.
   If it says "Author identity unknown", first run:

       git config --global user.name "Your Name"
       git config --global user.email "you@example.com"

4. Refresh the repo page on GitHub; you should see app/, sim/, tests/, docs/ and README.md.

No laptop of your own? Use github.com in a browser instead: open the new repo, click
**Add file -> Upload files**, drag in the unzipped folder contents, then **Commit changes**.
