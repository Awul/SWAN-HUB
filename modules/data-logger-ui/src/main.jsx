/**
 * Application Entry Point
 * 
 * Renders the main App component into the DOM root element.
 * StrictMode is enabled to highlight potential problems in the application.
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';

// Mount the React application to the DOM
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
