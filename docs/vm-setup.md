# Set up VM Hunter on your own Linux VM

This guide runs VM Hunter on a Linux VM that you own. Two workflows run in n8n on the VM:

- **Claim Oracle A1** keeps trying to claim a free Oracle Cloud Ampere A1 VM.
- **Watch offers** watches public sources for new free server offers.

Both post to Discord. Nothing in this guide costs money if you follow the Free Tier steps.

Work through the steps in order. Each step ends with a **Pass:** line. If you do not see that result, stop and check the troubleshooting table at the end before you go on.

Placeholders are written in angle brackets, like `<vm-user>`. Replace the whole thing, angle brackets included, with your own value. Never paste a real key, OCID, IP address or webhook URL into a file in the repository.

---

## 1. Check the prerequisites on the VM

You need a Linux VM with git, Docker and the Docker compose plugin. Commands run as the VM user. Running as root is fine too; if you do, your home folder is `/root`.

1. Log in to the VM and check each tool:

   ```bash
   git --version
   docker --version
   docker compose version
   ```

   **Pass:** each command prints a version number. If one says "command not found", install that tool first.

---

## 2. Open the editor through an SSH tunnel

n8n runs on the VM and is never published on a public port. You reach its editor from your laptop through an SSH tunnel.

1. On your **laptop**, open a terminal and run this, replacing the whole placeholder with your VM user and the VM's IP address:

   ```bash
   ssh -L 5678:localhost:5678 <vm-user>@<vm-ip>
   ```

   > **Warning:** replace the whole placeholder, including the angle brackets. If you leave them in, bash fails with `syntax error near unexpected token newline`.

2. Keep this terminal open. The tunnel works only while it is open.

3. On your laptop, open a browser at:

   ```
   http://localhost:5678
   ```

   **Pass:** the n8n editor page loads on your laptop. (It will ask you to create an owner account later, in step 7.)

---

## 3. Create a read-only GitHub deploy key and clone the repository

The VM gets its own key with read-only access to the repository.

1. On the **VM**, generate an ed25519 key with an empty passphrase and the comment `vm-hunter VM`:

   ```bash
   ssh-keygen -t ed25519 -C "vm-hunter VM" -f ~/.ssh/github_vmhunter
   ```

   When it asks for a passphrase, press Enter twice to leave it empty.

2. Add a Host block for the key to `~/.ssh/config`. Create the file if it does not exist. The block looks like this:

   ```
   Host github-vmhunter
     HostName github.com
     User git
     IdentityFile ~/.ssh/github_vmhunter
     IdentitiesOnly yes
   ```

3. Print the public key and copy the whole line:

   ```bash
   cat ~/.ssh/github_vmhunter.pub
   ```

4. In your browser, open the repository on GitHub, then go to **Settings > Deploy keys > Add deploy key**. Paste the public key. Leave **Allow write access** unchecked, then save.

5. Clone the repository on the VM with the `github-vmhunter` alias:

   ```bash
   git clone git@github-vmhunter:<github-owner>/vm-hunter.git ~/vm-hunter
   ```

   Replace `<github-owner>` with the GitHub account or organisation that owns the repository.

   **Pass:** `ls ~/vm-hunter` lists the project files, including `workflows` and `.env.example`.

---

## 4. Create the n8n folder outside the repository

n8n lives in `~/n8n`, outside the repository, so no secret can end up in git.

1. Create the folder and the two files you need. You will fill them in over the next steps:

   ```bash
   mkdir -p ~/n8n
   cd ~/n8n
   touch hunter.env compose.yaml
   chmod 600 hunter.env
   ```

   `hunter.env` holds every secret. It must never be committed.

2. Copy the pinned image tag and digest from the repository's `compose.yaml`. Open it and find the line that starts with `image:`:

   ```bash
   grep 'image:' ~/vm-hunter/compose.yaml
   ```

   The line looks like `image: n8nio/n8n:<tag>@sha256:<digest>`. Copy the tag and the digest exactly.

3. Write `~/n8n/compose.yaml`. Use the same image line you copied in step 2, and use your own path to the clone's `workflows` folder. For a root VM the path is `/root/vm-hunter/workflows`; for another user it is `/home/<vm-user>/vm-hunter/workflows`:

   ```yaml
   services:
     n8n:
       image: n8nio/n8n:<tag>@sha256:<digest>
       env_file: hunter.env
       ports:
         - "127.0.0.1:5678:5678"
       volumes:
         - n8n_data:/home/node/.n8n
         - /root/vm-hunter/workflows:/workflows:ro
       restart: unless-stopped

   volumes:
     n8n_data:
   ```

   - `ports` binds only to the VM's own localhost, so the port is never public. You reach it through the tunnel from step 2.
   - `n8n_data` is a named volume that keeps the n8n database.
   - `/workflows` is the read-only copy of the workflow files.

   > **Note:** the `compose.yaml` in the repository is for checks only. It has no ports and no data, so do not use it to run n8n.

   **Pass:** `docker compose -f ~/n8n/compose.yaml config` prints the configuration without an error.

---

## 5. Fill in hunter.env

n8n does not read `.env.example`. Every variable a workflow needs must be in `~/n8n/hunter.env`.

1. List the variable names in `.env.example` and compare them with `hunter.env`. This loop prints every name that is missing from `hunter.env`:

   ```bash
   cd ~/n8n
   grep -oE '^[A-Z0-9_]+=' ~/vm-hunter/.env.example | tr -d '=' | sort > /tmp/example-names.txt
   grep -oE '^[A-Z0-9_]+=' hunter.env | tr -d '=' | sort > /tmp/env-names.txt
   comm -23 /tmp/example-names.txt /tmp/env-names.txt
   ```

   The only names the loop should print are the optional `HUNTER_DISCORD_THREAD_<KIND>` names (`OFFERS`, `CLAIMS`, `FAILURES`, `SUMMARY`). Every other name it prints is missing and must be added.

2. Add the non-secret defaults to `hunter.env`, copied from `.env.example`:

   ```
   HUNTER_FREE_FOR_DEV_URL=https://raw.githubusercontent.com/ripienaar/free-for-dev/master/README.md
   HUNTER_LOWENDBOX_FEED_URL=https://lowendbox.com/feed/
   NODE_FUNCTION_ALLOW_BUILTIN=crypto
   ```

   > **Warning:** if a feed URL is missing, the Fetch free-for-dev step fails with `URL parameter must be a string, got undefined`. Check that both feed URLs are present.

3. Add the n8n settings:

   ```
   N8N_DIAGNOSTICS_ENABLED=false
   N8N_SECURE_COOKIE=false
   GENERIC_TIMEZONE=<your-IANA-zone>
   TZ=<your-IANA-zone>
   ```

   Use the same IANA zone for both `GENERIC_TIMEZONE` and `TZ`, for example `Europe/Berlin`. The daily counters roll over in this zone.

   `N8N_SECURE_COOKIE=false` is safe only because the port is bound to `127.0.0.1` and you reach it through the SSH tunnel. Do not use this setting if you open the port any other way.

4. Add the Discord, Oracle and other values in steps 6, 10 and 11. They are filled in later.

5. Run the check loop from step 1 again once everything is filled in.

   **Pass:** the loop prints only `HUNTER_DISCORD_THREAD_<KIND>` names, or nothing.

---

## 6. Create the Discord webhook

1. In Discord, open the **forum channel** (not a thread). Choose **Edit Channel > Integrations > Webhooks > New Webhook > Copy Webhook URL**.

2. Add the values to `~/n8n/hunter.env`:

   ```
   HUNTER_DISCORD_WEBHOOK_URL=<webhook-url-you-copied>
   HUNTER_DISCORD_THREAD_ID=<thread-id>
   ```

   `HUNTER_DISCORD_THREAD_ID` is the id of the thread (the room) that every message goes to. The per-kind thread variables are optional overrides.

> **Warning:** the webhook URL is a secret. Anyone who has it can post to your channel. If it is ever pasted anywhere (a chat, a screenshot, a repository), delete the webhook in Discord and create a new one.

**Pass:** `grep -c '^HUNTER_DISCORD_WEBHOOK_URL=.' ~/n8n/hunter.env` prints `1`. Do not print the value itself.

---

## 7. Start n8n and import the workflows

1. Start n8n:

   ```bash
   cd ~/n8n
   docker compose up -d
   ```

2. Import the workflows from the clone:

   ```bash
   docker compose exec n8n n8n import:workflow --separate --input=/workflows
   ```

3. Restart the container so it reads `hunter.env` again:

   ```bash
   docker compose up -d --force-recreate
   ```

   > **Warning:** after you change `hunter.env`, always use `docker compose up -d --force-recreate`. `docker compose restart` does not reload `env_file`, so your changes are ignored.

4. Open the editor at `http://localhost:5678` on your laptop (through the tunnel). Create the owner account when it asks.

5. Check that the workflows are listed: **Claim Oracle A1**, **Watch offers**, **Notify Discord** and **Error report**.

   **Pass:** all four workflows appear in the list.

---

## 8. Smoke test: send a Discord message

This test checks that the notify workflow reaches your thread.

1. In the editor, create a new workflow. Paste the JSON only onto the empty canvas, never into a Code node.

2. Build it with these three nodes:
   - **Manual Trigger**.
   - **Code** node with this code. It returns one item with `kind` set to `summary`:

     ```javascript
     return [{ json: { kind: 'summary', title: 'VM smoke test', body: 'Hello from the VM n8n' } }];
     ```

   - **Execute Workflow** node that calls the workflow with id `notifyDiscord01`.

   The Code node is needed because Execute Workflow passes incoming items through unchanged, so it must receive the message fields.

3. Click **Execute workflow**.

   **Pass:** the message `VM smoke test` appears in the thread on Discord.

4. Delete this test workflow when you are done.

---

## 9. Smoke test: error reporting

This test checks that a failing automatic run is reported to Discord.

1. Create a new workflow with:
   - A **Schedule Trigger** set to every 1 minute.
   - A **Code** node with this code:

     ```javascript
     throw new Error('smoke failure');
     ```

2. Open **Settings** of this workflow and set **Error Workflow** to **Error report**. Save.

3. Activate the workflow and wait 1-2 minutes.

4. Deactivate the workflow at once, so it does not keep failing.

   **Pass:** a failure report for `smoke failure` appears in the thread on Discord.

Error workflows run only for automatic runs. A manual execution does not trigger them.

> **If n8n refuses to activate** with `Please resolve outstanding issues before you activate it`, click **Clear execution**, save the workflow, then activate it again.

When you are done, delete this test workflow.

---

## 10. Set up the Oracle Cloud account and API key

Stay on the **Free Tier**. Never upgrade to Pay As You Go. The cost guard matters most on upgraded accounts, where a request could be charged.

1. Log in at `https://cloud.oracle.com` with your **Cloud Account Name**.

2. On the **VM**, generate the API key. The private key never leaves the VM:

   ```bash
   openssl genrsa -out ~/oci-api-key.pem 2048
   chmod 600 ~/oci-api-key.pem
   openssl rsa -pubout -in ~/oci-api-key.pem -out ~/oci-api-key-public.pem
   ```

   **Pass:** `~/oci-api-key.pem` and `~/oci-api-key-public.pem` exist.

3. Print the public key and copy the whole output. This is the only part you paste into the console:

   ```bash
   cat ~/oci-api-key-public.pem
   ```

4. In the Oracle console, go to **profile > My profile > Tokens and keys > API keys > Add API key > Paste a public key**, paste the public key, and add it.

   Older consoles: **Identity & Security > Domains > Default > Users > your user > API keys**.

5. Get the fingerprint on the VM:

   ```bash
   openssl rsa -pubout -outform DER -in ~/oci-api-key.pem | openssl md5 -c
   ```

   **Pass:** the console shows the same fingerprint for the new key. Copy the fingerprint into `hunter.env` as `HUNTER_OCI_KEY_FINGERPRINT`.

6. Get your user OCID. In Oracle Cloud Shell, run `echo $OCI_CS_USER_OCID`, or open **My profile > Details** and copy the OCID. Put it in `hunter.env` as `HUNTER_OCI_USER_OCID`.

---

## 11. Collect the other Oracle values in Cloud Shell

Open Oracle Cloud Shell in the console. Set the tenancy id and check each value:

1. Save the tenancy OCID:

   ```bash
   T=$OCI_TENANCY
   echo $T
   ```

2. List the compartments. This may print nothing when there are no sub-compartments, which is fine. Use the tenancy OCID as the compartment:

   ```bash
   oci iam compartment list --compartment-id $T
   ```

3. List the availability domains:

   ```bash
   oci iam availability-domain list --compartment-id $T
   ```

4. List the subnets:

   ```bash
   oci network subnet list --compartment-id $T
   ```

   If the list is empty, create a network first: in the console go to **Networking > Virtual cloud networks > Start VCN Wizard > Create VCN with Internet Connectivity**. This option is free. Then run the command again.

5. Find the Ubuntu image. The image name must contain `aarch64`, because A1 is an Arm shape:

   ```bash
   oci compute image list --compartment-id $T --operating-system "Canonical Ubuntu" --operating-system-version "22.04" --shape VM.Standard.A1.Flex
   ```

6. Put the values in `~/n8n/hunter.env`:

   | Variable | Value |
   |---|---|
   | `HUNTER_OCI_TENANCY_OCID` | the tenancy OCID from step 1 |
   | `HUNTER_OCI_COMPARTMENT_OCID` | the tenancy OCID again, so the cost guard counts everything |
   | `HUNTER_OCI_AVAILABILITY_DOMAIN` | the availability domain name from step 3 |
   | `HUNTER_OCI_SUBNET_OCID` | the subnet OCID from step 4 |
   | `HUNTER_OCI_IMAGE_OCID` | the `aarch64` Ubuntu 22.04 image OCID from step 5 |
   | `HUNTER_OCI_API_BASE` | `https://iaas.<home-region>.oraclecloud.com` (your home region, for example `eu-frankfurt-1`) |

7. Put the private key into `hunter.env` as one line. Run this on the VM. It appends the line to the file, so run it only once:

   ```bash
   echo "HUNTER_OCI_PRIVATE_KEY_PEM=$(awk '{printf "%s\\n", $0}' ~/oci-api-key.pem)" >> ~/n8n/hunter.env
   ```

8. Put the laptop's public key line into `hunter.env` as `HUNTER_OCI_SSH_PUBLIC_KEY`. Copy the one line from your laptop's `.pub` file (for example `~/.ssh/id_ed25519.pub`). `ssh-keygen` prints no key, so open the file to copy it.

   **Pass:** the check loop from step 5 prints only the optional `HUNTER_DISCORD_THREAD_<KIND>` names.

---

## 12. Check the A1 limit before the claimer starts

Your tenancy may allow less than the Free Tier maximum. The owner's Free Tier tenancy allows **2 OCPU and 12 GB**, not 4 and 24. If you ask for more, Oracle answers `HTTP 400 LimitExceeded` and the claimer stops.

1. In Cloud Shell, check the limits:

   ```bash
   oci limits resource-availability get --compartment-id $T --service-name compute --limit-name standard-a1-core-count
   oci limits resource-availability get --compartment-id $T --service-name compute --limit-name standard-a1-memory-count
   ```

2. Set both sizes in `~/n8n/hunter.env` to the limit you see. For the owner's tenancy:

   ```
   HUNTER_OCI_A1_OCPUS=2
   HUNTER_OCI_A1_MEMORY_GB=12
   ```

   If the limit you see is different, use that number instead.

3. Apply the change with a force-recreate (see step 7):

   ```bash
   cd ~/n8n
   docker compose up -d --force-recreate
   ```

   A stopped claimer starts again by itself once its settings change.

   **Pass:** `oci limits resource-availability get` shows the limit, and `hunter.env` holds the same two numbers.

---

## 13. Activate the workflows

1. In the editor, open **Watch offers** and **Claim Oracle A1**. Set each one to **Active**.

2. Re-check both after every import. An import can leave them inactive.

> **Warning:** never click **Execute** on **Watch offers**. A manual run does not save the seen list, so the same offers would be reported again.

What to expect:

- **Watch offers** first runs about 6 hours after activation. Its first run posts nothing.
- **Claim Oracle A1** attempts about every 2 minutes. Each attempt waits at least 60 seconds plus a random delay.
- Each workflow posts a **summary at 09:00 local time**. A missing summary means n8n or the VM is down.

To confirm that **Claim Oracle A1** works:

1. Open **Executions** and choose a run where the **Read response** step ran.
2. Open the **Logs** panel and click **Read response**.
3. If the outcome is `capacity`, the claimer is working. It means Oracle has no free A1 capacity right now, and it will try again.

**Pass:** both workflows show **Active**, and a run shows `capacity` in Read response.

---

## 14. Update to a new version

When the repository changes, run these commands on the VM.

1. Pull the latest code:

   ```bash
   cd ~/vm-hunter && git pull
   ```

2. Import the workflows and restart n8n:

   ```bash
   cd ~/n8n
   docker compose exec n8n n8n import:workflow --separate --input=/workflows
   docker compose up -d --force-recreate
   ```

3. Check that the workflows are still active. Activate any that are not.

   **Pass:** **Watch offers** and **Claim Oracle A1** show **Active**.

---

## 15. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `bash: syntax error near unexpected token newline` when you run the SSH command | You left the angle brackets in the placeholder. | Replace the whole `<vm-user>@<vm-ip>` placeholder, angle brackets included, with your values. |
| `URL parameter must be a string, got undefined` in Fetch free-for-dev | `HUNTER_FREE_FOR_DEV_URL` or the LowEndBox feed URL is missing from `hunter.env`. | Add the missing URL (step 5), then run `docker compose up -d --force-recreate`. |
| A message says a variable is missing (the name is in the message) | `hunter.env` has no value for that variable. | Add the name to `hunter.env`, then run `docker compose up -d --force-recreate`. Use the check loop in step 5 to find all missing names. |
| You changed `hunter.env` but nothing changed | `docker compose restart` does not reload `env_file`. | Run `docker compose up -d --force-recreate`. |
| `A1 claim stopped: HTTP 401` | The key, the fingerprint or the OCID is wrong. | Check `HUNTER_OCI_KEY_FINGERPRINT`, `HUNTER_OCI_USER_OCID` and `HUNTER_OCI_TENANCY_OCID`, and that the public key in the console matches `~/oci-api-key.pem`. |
| `Cost guard refused` | Your account already uses A1 or storage, and the guard counts it. | Look at the existing instances and volumes in the console. The claimer starts again by itself once its settings change. |
| `HTTP 400 LimitExceeded` | The requested size is above your tenancy's A1 limit. | Set `HUNTER_OCI_A1_OCPUS` and `HUNTER_OCI_A1_MEMORY_GB` to the limit from step 12, then force-recreate. |
| `Please resolve outstanding issues before you activate it` | A test workflow has an unresolved execution issue. | Click **Clear execution**, save, then activate. |
| A workflow is inactive after an import | Imports can leave workflows inactive. | Open the workflow and set it to **Active** again. |
| Outcome `capacity` in Read response | Oracle has no free A1 capacity right now. | This is normal. The claimer is working and will try again. |
| No Watch offers post after the first run | The first run posts nothing by design. | Wait for the second run. |
| No summary at 09:00 local time | n8n or the VM is down. | Check that the VM is on and `docker compose ps` in `~/n8n` shows the container running. |
