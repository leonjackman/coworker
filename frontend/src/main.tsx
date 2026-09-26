import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { SoundProvider } from './components/sound-provider';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <SoundProvider>
      <App />
    </SoundProvider>
  </React.StrictMode>
);
