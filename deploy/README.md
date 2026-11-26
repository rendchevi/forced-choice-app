# Hosting on this workstation with a Cloudflare named tunnel

The study servers listen on 127.0.0.1 only; `cloudflared` connects out to Cloudflare and serves
them at fixed https addresses. Nothing is opened in the firewall. The workstation has to stay on
(and the servers and the tunnel running) for as long as a Prolific study is live.

Needs: a Cloudflare account with a domain added to it (its DNS managed by Cloudflare).

## Once (done 2026-11-24)

```bash
cloudflared tunnel login                         # browser; picked reneren.xyz; wrote ~/.cloudflared/cert.pem
cloudflared tunnel create forced-choice          # id 978e3e7f-3ada-406d-bde4-dff163bcc87d, credentials in ~/.cloudflared/
cloudflared tunnel route dns forced-choice chain.reneren.xyz
cloudflared tunnel route dns forced-choice typicality.reneren.xyz
cp deploy/cloudflared.yml ~/.cloudflared/forced-choice.yml   # ids filled in
```

The config has its own file name (not `config.yml`), so it never mixes with other tunnels on
this machine.

## Each study period

Run each in its own tmux window so they survive closing the terminal (`tmux new -s study`,
then `Ctrl-b c` for a new window):

```bash
conda activate lingu
cd studies/chain && python run.py 2>&1 | tee -a server.log          # window 1
cd studies/typicality && python run.py 2>&1 | tee -a server.log     # window 2
cloudflared tunnel --config ~/.cloudflared/forced-choice.yml run   # window 3
```

Check from outside: `https://chain.reneren.xyz/?sid=test1--spec=<spec>--chain=1`.

## Prolific study URLs

- Chain (one Prolific study per spec):
  `https://chain.reneren.xyz/?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}&spec=<spec>`
- Typicality:
  `https://typicality.reneren.xyz/?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}`

On Prolific, choose "I'll use URL parameters", and add the study's
`completion_code` (action: manually review). Typicality only (its consent page still has a
decline option): also add `no_consent_code` (action e.g. "Request return"). The chain consent
page has a single checkbox; someone who does not consent returns the study themselves.

## Monitoring

`https://chain.reneren.xyz/chain/admin?key=<admin_api_key.txt>` and
`https://typicality.reneren.xyz/typ/admin?key=<admin_api_key.txt>`. The admin key is also
Potato's admin key: keep the link private.
