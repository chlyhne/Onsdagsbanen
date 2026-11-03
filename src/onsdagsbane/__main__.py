import argparse
import logging
import os
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
from .app import Application


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    # Never log OAuth authorization codes or state from query strings.
    def log_request(self,code='-',size='-'):
        self.log_message('%s %s %s',self.command,self.path.split('?')[0],code)


def main():
    parser = argparse.ArgumentParser(description='Onsdagsbanens hjemmeside')
    parser.add_argument('--demo',action='store_true',help='Isoleret demonstration med lokal administrator')
    parser.add_argument('--preview-admin',action='store_true',help='Tilbyd midlertidigt lokalt administratorlogin i to timer')
    parser.add_argument('--port',type=int,default=8090)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--db')
    args = parser.parse_args()
    if (args.demo or args.preview_admin) and args.host not in ('127.0.0.1','localhost','::1'):
        parser.error('Demo og midlertidigt preview-login må kun bindes til loopback.')
    logging.basicConfig(level=logging.INFO)
    app = Application(db_path=args.db or ('build/onsdagsbane/demo-v1.sqlite3' if args.demo else None),base_url=os.getenv('BASE_URL',f'http://{args.host}:{args.port}'),demo=args.demo,preview_admin=args.preview_admin)
    with make_server(args.host,args.port,app,server_class=ThreadedServer,handler_class=QuietHandler) as server:
        print(f'Onsdagsbanen: http://{args.host}:{args.port}',flush=True)
        server.serve_forever()


if __name__=='__main__':
    main()
