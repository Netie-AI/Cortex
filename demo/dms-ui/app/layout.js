import "./globals.css";
import { RoleProvider } from "../context/RoleContext";

export const metadata = {
  title: "CortexOS · DMS",
  description: "CortexOS DMS Warehouse Demo",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className="dark" data-theme="dark">
      <body>
        <RoleProvider>{children}</RoleProvider>
      </body>
    </html>
  );
}