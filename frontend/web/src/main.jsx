import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import AdminApp from './admin/AdminApp.jsx';
import Tv from './screens/Tv.jsx';
import './styles/tokens.css';
import './styles/base.css';

// Three surfaces, one build: player app, venue TV at /tv, operator admin at /admin.
const path = window.location.pathname;
const Root = path.startsWith('/admin') ? AdminApp : path === '/tv' ? Tv : App;

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
