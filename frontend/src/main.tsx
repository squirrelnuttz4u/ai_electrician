import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import "./styles.css";
import { Layout } from "./Layout";
import { MachinesPage } from "./pages/MachinesPage";
import { MachinePage } from "./pages/MachinePage";
import { ViewerPage } from "./pages/ViewerPage";

const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <MachinesPage /> },
      { path: "machines/:machineId", element: <MachinePage /> },
      { path: "prints/:printId", element: <ViewerPage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
);
