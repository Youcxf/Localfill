# localfill dashboard

The Angular app for localfill. Node 22 or 24 (not an odd numbered release such as 25).

```bash
npm ci
npm start          # http://localhost:4200, /api is passed on to the backend on port 8011
npm test           # unit tests
npm run build      # production build, in dist/localfill/browser
```

The logo (`logo.png`) and the browser tab icon (`icon.png`) live in the repository root. `npm start` and `npm run build` copy them into `public/`, so replace the ones in the root, not the copies.

Start the backend first. How the app is built, which screen uses which endpoint and how it gets into the Docker image is in [docs/frontend.md](../docs/frontend.md).
