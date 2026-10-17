// Standalone SCM probe. No integration modules, child processes, devices or remote requests.
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security;
using System.Security.Authentication;
using System.Security.Cryptography.X509Certificates;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;

public static class StagingRegistration
{
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr OpenSCManager(string machine, string database, uint access);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateService(IntPtr manager, string name, string display, uint access,
        uint type, uint start, uint error, string binary, string group, IntPtr tag,
        string dependencies, string account, string password);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool CloseServiceHandle(IntPtr handle);

    // Operator helper only. Loading this assembly never calls this method or Main.
    // Fixed staging identity, no account fallback, no retry and no service start.
    public static void Create(string executable)
    {
        if (!Path.IsPathRooted(executable) || Path.GetFileName(executable) != "FPFPhase1Staging.exe")
            throw new ArgumentException("Unexpected staging executable.");
        IntPtr manager = OpenSCManager(null, null, 0x0003); // CONNECT | CREATE_SERVICE
        if (manager == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            IntPtr service = CreateService(manager, "FPFPhase1Staging", "FPF Phase1 Staging Probe",
                0x0001, 16, 3, 1, "\"" + executable + "\"", null, IntPtr.Zero, null,
                "NT SERVICE\\FPFPhase1Staging", null); // Virtual accounts require a NULL password.
            if (service == IntPtr.Zero)
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            CloseServiceHandle(service);
        }
        finally { CloseServiceHandle(manager); }
    }
}

public static class StagingProbe
{
    // All client socket paths pass this check BEFORE DNS or network operations.
    public static void RequireEndpoint(string host, int port, int approvedPort, bool udp)
    {
        if (udp || host != "127.0.0.1" || port != approvedPort || port < 1 || port > 65535)
            throw new SecurityException("staging_endpoint_denied");
    }

    public static bool Rejects(string host, int port, int approvedPort, bool udp)
    {
        try { RequireEndpoint(host, port, approvedPort, udp); return false; }
        catch (SecurityException) { return true; }
    }

    public static bool CheckPeer(X509Certificate2 expected, X509Certificate actual,
                                 X509Chain chain, SslPolicyErrors errors)
    {
        if (actual == null || Convert.ToBase64String(expected.RawData) != Convert.ToBase64String(actual.GetRawCertData())
            || DateTime.Now < expected.NotBefore || DateTime.Now > expected.NotAfter) return false;
        if (errors == SslPolicyErrors.None) return true;
        // Trust ONLY the exact temporary self-signed certificate, without changing a store.
        // Name mismatch, expiry and every chain error except UntrustedRoot remain fatal.
        if (errors != SslPolicyErrors.RemoteCertificateChainErrors || chain == null || chain.ChainStatus.Length == 0) return false;
        foreach (X509ChainStatus status in chain.ChainStatus)
            if (status.Status != X509ChainStatusFlags.UntrustedRoot) return false;
        return true;
    }

    public static bool LocalHttps(string certificatePath, int port)
    {
        RequireEndpoint("127.0.0.1", port, port, false);
        using (var cert = new X509Certificate2(certificatePath))
        {
            if (cert.Subject != "CN=localhost" || cert.Issuer != cert.Subject
                || DateTime.Now < cert.NotBefore || DateTime.Now > cert.NotAfter) throw new SecurityException("invalid_local_certificate");
            foreach (X509Extension extension in cert.Extensions)
                // A synthetic certificate must not trigger online AIA/CRL retrieval.
                if (extension.Oid.Value == "1.3.6.1.5.5.7.1.1" || extension.Oid.Value == "2.5.29.31")
                    throw new SecurityException("external_certificate_metadata_denied");
            // Raw literal-loopback TCP has no DNS, HTTP proxy or redirect machinery.
            using (var client = new TcpClient())
            {
                var connect = client.BeginConnect(IPAddress.Loopback, port, null, null);
                using (connect.AsyncWaitHandle)
                    if (!connect.AsyncWaitHandle.WaitOne(5000)) throw new System.TimeoutException();
                client.EndConnect(connect);
                using (var stream = new SslStream(client.GetStream(), false,
                    delegate(object sender, X509Certificate actual, X509Chain chain, SslPolicyErrors errors)
                    { return CheckPeer(cert, actual, chain, errors); }))
                {
                    stream.ReadTimeout = 5000; stream.WriteTimeout = 5000;
                    stream.AuthenticateAsClient("localhost", null, SslProtocols.Tls12, false);
                    byte[] request = Encoding.ASCII.GetBytes("GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n");
                    stream.Write(request, 0, request.Length); stream.Flush();
                    if (ReadHeaders(stream) != "HTTP/1.1 200 OK\r\nContent-Length: 19\r\nConnection: close\r\n\r\n") return false;
                    var body = new byte[19];
                    int length = 0;
                    while (length < body.Length)
                    {
                        int count = stream.Read(body, length, body.Length - length);
                        if (count == 0) break;
                        length += count;
                    }
                    return length == 19 && Encoding.ASCII.GetString(body) == "STAGE-SYNTHETIC-OK\n";
                }
            }
        }
    }

    static string ReadHeaders(Stream stream)
    {
        var text = new StringBuilder();
        while (text.Length < 1024)
        {
            int value = stream.ReadByte();
            if (value < 0) break;
            text.Append((char)value);
            if (text.ToString().EndsWith("\r\n\r\n", StringComparison.Ordinal)) return text.ToString();
        }
        throw new IOException("invalid_mock_headers");
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security,
                                           uint disposition, uint flags, IntPtr template);

    static bool AccessDenied(string path, uint access)
    {
        // OPEN_EXISTING checks rights without writing, deleting or changing an ACL.
        using (SafeFileHandle handle = CreateFile(path, access, 7, IntPtr.Zero, 3, 0, IntPtr.Zero))
            return handle.IsInvalid && Marshal.GetLastWin32Error() == 5;
    }

    public static bool Run(string code)
    {
        string data = Path.Combine(Directory.GetParent(code).FullName, "data");
        var checks = new SortedDictionary<string, bool>();
        bool passed = false;
        try
        {
            using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
            {
                string expectedSid = File.ReadAllText(Path.Combine(code, "service-sid.txt")).Trim();
                checks["dedicated_service_token"] = identity.User.Value == expectedSid && expectedSid.StartsWith("S-1-5-80-", StringComparison.Ordinal);
                checks["not_system_or_admin"] = !identity.IsSystem && !new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
                if (!checks["dedicated_service_token"] || !checks["not_system_or_admin"]) throw new SecurityException("wrong_token");
            }
            string canary = Path.Combine(code, "synthetic-readonly.txt");
            checks["code_read_allowed"] = File.ReadAllText(canary) == "STAGE-SYNTHETIC-READONLY";
            checks["code_write_denied"] = AccessDenied(canary, 0x40000000);
            checks["code_delete_denied"] = AccessDenied(canary, 0x00010000);
            checks["code_acl_change_denied"] = AccessDenied(canary, 0x00040000);
            bool createDenied = false;
            try { using (File.Create(Path.Combine(code, "unexpected-code-write-" + Guid.NewGuid().ToString("N")))) { } }
            catch (UnauthorizedAccessException) { createDenied = true; }
            checks["code_create_denied"] = createDenied;
            string sample = Path.Combine(data, "synthetic-access-" + Guid.NewGuid().ToString("N") + ".txt");
            using (var file = new FileStream(sample, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None))
            {
                byte[] value = Encoding.ASCII.GetBytes("STAGE-SYNTHETIC-DATA");
                file.Write(value, 0, value.Length); file.Flush(true); file.Position = 0;
                checks["data_create_read_write_allowed"] = new StreamReader(file).ReadToEnd() == "STAGE-SYNTHETIC-DATA";
            }
            using (SafeFileHandle handle = CreateFile(sample, 0x80000000 | 0x40000000 | 0x00010000, 7, IntPtr.Zero, 3, 0, IntPtr.Zero))
                checks["data_modify_access_allowed"] = !handle.IsInvalid;
            checks["external_tcp_denied_before_socket"] = Rejects("192.0.2.1", 4370, 12345, false);
            checks["dns_names_denied_before_resolution"] = Rejects("stage-external.invalid", 443, 12345, false);
            checks["other_loopback_ports_denied"] = Rejects("127.0.0.1", 4370, 12345, false);
            checks["udp_denied_before_socket"] = Rejects("127.0.0.1", 12345, 12345, true);
            var endpoint = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(
                File.ReadAllText(Path.Combine(code, "mock-endpoint.json")));
            int port = Convert.ToInt32(endpoint["port"]);
            checks["localhost_https_synthetic_exchange"] = LocalHttps(Path.Combine(code, "localhost.cer"), port);
            passed = true;
            foreach (bool value in checks.Values) passed &= value;
        }
        catch { checks["probe_completed_without_error"] = false; }
        var report = new Dictionary<string, object>();
        report["schema"] = "phase1-service-token-probe-v1";
        report["status"] = passed ? "PASS" : "FAIL";
        report["checks"] = checks;
        report["scope"] = "dedicated_probe_only_not_durable_engine_or_kernel_packet_drop_proof";
        report["off_host_packets_attempted"] = false;
        // A single run only: never replace an earlier result or print raw exceptions.
        using (var file = new FileStream(Path.Combine(data, "step4-result.json"), FileMode.CreateNew, FileAccess.Write))
        using (var writer = new StreamWriter(file)) writer.Write(new JavaScriptSerializer().Serialize(report));
        return passed;
    }
}

public sealed class StagingService : ServiceBase
{
    public StagingService() { ServiceName = "FPFPhase1Staging"; AutoLog = false; CanStop = true; }
    protected override void OnStart(string[] args)
    {
        ThreadPool.QueueUserWorkItem(delegate
        {
            try { ExitCode = StagingProbe.Run(AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar)) ? 0 : 1; }
            catch { ExitCode = 2; }
            // Remain Running until the operator helper reads the result and stops
            // this staging service. This avoids a short-lived SCM startup race.
        });
    }
    public static void Main() { ServiceBase.Run(new StagingService()); }
}
