import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import Tv from './screens/Tv.jsx';
import './styles/tokens.css';
import './styles/base.css';

// Two surfaces, one build: the player app everywhere, the venue TV at /tv
// (unauthenticated, work order §2.3.7). No router dependency for one fork.
const Root = window.location.pathname === '/tv' ? Tv : App;

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
