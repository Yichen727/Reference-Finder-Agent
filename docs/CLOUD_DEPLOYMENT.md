# Deploy one Streamlit Community Cloud app

The cloud entry starts the existing FastAPI backend inside the same instance, then
loads the existing Streamlit interface. Only Streamlit has a public URL. The API
binds to a dynamically assigned loopback port, and the frontend connects internally.
Local `run.py` and the Windows launcher keep their existing behaviour.

## Deployment steps

1. Commit and push this project, including `cloud/`, `requirements.txt`, `backend/`,
   `frontend/`, `config/`, `prompts/`, and the packaged PowerPoint templates.
2. Create an app in Streamlit Community Cloud and select the repository and branch.
3. Set **Main file path** to **`cloud/streamlit_app.py`**. App URL is optional.
4. Open **Advanced settings**, select **Python 3.12**, and paste this into **Secrets**:

   ```toml
   OPENAI_API_KEY = "your-api-key"
   APP_ACCESS_CODE = "your-private-access-code"
   ```

   Optional model choices can be added using `OPENAI_MODEL` and `OPENAI_SEARCH_MODEL`.
   Copy their values from your local configuration if you want identical models.
   [cloud/secrets.toml.example](../cloud/secrets.toml.example) is a ready-to-copy template.
   Do not upload `.env` or an actual `secrets.toml` file.
5. Deploy. Each new browser session must enter the security code to unlock the app.
   Without a configured code, access remains blocked. The internal API starts only
   after successful verification and becomes healthy before rendering the UI.
   Startup does not call OpenAI. Research requests use the configured account as usual.

No backend URL or separate API deployment is required. The entry configures `BACKEND_URL`
automatically. The cloud entry ignores local `.env` files; backend options come from
Streamlit Secrets or environment variables.

`requirements.txt` installs the runtime dependencies declared in `pyproject.toml`.
The Windows development lockfile is not used for cloud deployment. The entry-specific
`cloud/.streamlit/config.toml` enables the public Streamlit listener while preserving
the repository's loopback-only local configuration and shared theme.

## Data and runtime behaviour

- Research, Favorites, Packs, previews and exports use `data/cloud/` by default, separately
  from the local workspace's `data/`. `DATA_DIR` can select another local directory.
- Community Cloud does **not guarantee persistence of local files**. This entry does not
  add external storage; long-term saved research requires a separate persistence solution.
- This is a shared workspace. Visitors share the same research, Favorites and Packs.
  Use the platform's app visibility controls for the audience you intend to support.
- Normal Streamlit reruns, navigation and multiple sessions reuse one API worker.
  Closing a browser tab does not stop it. Closing the Streamlit process closes the
  parent pipe, allowing the worker to stop without touching unrelated processes.
- Clearing resource caches restarts the internal worker. Running searches may be
  interrupted; completed saved records remain available if the local files remain.
- After changing backend files, source configuration or Secrets, use **Reboot app**
  in Streamlit's app settings. The entry detects incompatible cached settings/code
  and asks for a reboot instead of silently using stale backend code.

## Troubleshooting

| Message | Action |
|---|---|
| Access is not configured | Add APP_ACCESS_CODE as a quoted string in Secrets and reboot. |
| Add OPENAI_API_KEY | Add the key in Advanced settings > Secrets and reboot. |
| Check the values in your Streamlit Secrets | Check TOML types and backend setting values; error messages do not echo credentials. |
| The internal API could not start | Check app logs and writable storage, then reboot. |
| App settings or backend files changed | Reboot the app to load the new backend/settings. |

Official references: [deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy),
[dependencies](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies),
[entry-specific configuration](https://docs.streamlit.io/deploy/streamlit-community-cloud/status),
[Secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management),
and [local-storage limitations](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data).
