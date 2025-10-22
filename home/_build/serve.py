import http.server
import socketserver
import webbrowser
import yaml

with open('metadata.yml') as f:
    context = yaml.load(f, Loader=yaml.FullLoader)

class GithubPagesHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path[-1] != '/' and "." not in self.path:
            self.path += ".html"
        return http.server.SimpleHTTPRequestHandler.do_GET(self)

port = 8081
url = "http://localhost:" + str(port) + "/static/"
webbrowser.open(url)
my_server = socketserver.TCPServer(("", port), GithubPagesHandler)
my_server.serve_forever()
