import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
import './styles/qnet-site.css';
import './styles/qnet-site-colors.css';
import { startAppUpdates } from './lib/appUpdate';
import { installDiagnostics } from './lib/diagnostics';

installDiagnostics();
startAppUpdates();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
