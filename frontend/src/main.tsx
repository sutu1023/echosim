import { Workspace } from './Workspace';
import { ModelTest } from './ModelTest';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './style.css';
import './editorial.css';
import './simulation-layout.css';

createRoot(document.getElementById('root')!).render(window.location.pathname === '/dev/model-test' ? <ModelTest /> : window.location.pathname === "/" ? <App /> : <Workspace />);
