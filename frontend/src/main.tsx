import { ModelTest } from './ModelTest';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './style.css';

createRoot(document.getElementById('root')!).render(window.location.pathname === '/dev/model-test' ? <ModelTest /> : <App />);
