# -*- coding: utf-8 -*-
"""
Clase Downloader
Downloader(url, path [, filename, headers, resume])

  url : string - url para descargar
  path : string - Directorio donde se guarda la descarga
  filename : [opt] string - Nombre de archivo para guardar
  headers : [opt] dict - Headers para usar en la descarga
  resume : [opt] bool - continuar una descarga previa en caso de existir, por defecto True


metodos:
  start() Inicia la descarga
  stop(erase = False)  Detiene la descarga, con erase = True elimina los datos descargados

"""
import mimetypes
import os
import re
import sys
import time
import urllib
import urllib2
import urlparse
from threading import Thread, Lock

import signal


class Downloader:
    # Informacion:
    @property
    def state(self):
        return self._state

    @property
    def connections(self):
        return len([c for c in self._download_info["parts"] if
                    c["status"] in [self.states.downloading, self.states.connecting]]), self._max_connections

    @property
    def downloaded(self):
        return self.__change_units__(sum([c["current"] - c["start"] for c in self._download_info["parts"]]))

    @property
    def average_speed(self):
        return self.__change_units__(self._average_speed)

    @property
    def speed(self):
        return self.__change_units__(self._speed)

    @property
    def remaining_time(self):
        if self.speed[0] and self._file_size:
            t = (self.size[0] - self.downloaded[0]) / self.speed[0]
        else:
            t = 0

        return time.strftime("%H:%M:%S", time.gmtime(t))

    @property
    def download_url(self):
        return self.url

    @property
    def size(self):
        return self.__change_units__(self._file_size)

    @property
    def progress(self):
        if self._file_size:
            return float(self.downloaded[0]) * 100 / float(self._file_size)
        elif self._state == self.states.completed:
            return 100
        else:
            return 0

    @property
    def filename(self):
        return self._filename

    @property
    def fullpath(self):
        return os.path.abspath(os.path.join(self._path, self._filename))

    # Funciones
    def start(self):
        if self._state == self.states.error: return

        self._start_time = time.time() - 1
        self._state = self.states.downloading

        for t in self._threads: t.start()
        self._speed_thread.start()

    def stop(self, erase=False):
        if self._state == self.states.downloading:
            # Detenemos la descarga
            self._state = self.states.stopped
            for t in self._threads:
                if t.isAlive(): t.join()

            # Guardamos la info al final del archivo
            self.file.seek(0, 2)
            offset = self.file.tell()
            self.file.write(str(self._download_info))
            self.file.write("%0.16d" % offset)

        self.file.close()

        if erase: os.remove(os.path.join(self._path, self._filename))

    def __speed_metter__(self):
        self._speed = 0
        self._average_speed = 0

        # downloaded = self._start_downloaded
        # downloaded2 = self._start_downloaded
        # t = time.time()
        # t2 = time.time()
        # time.sleep(1)

        while self.state == self.states.downloading:
            self._average_speed = (self.downloaded[0] - self._start_downloaded) / (time.time() - self._start_time)
            self._speed = (self.downloaded[0] - self._start_downloaded) / (time.time() - self._start_time)
            # self._speed = (self.downloaded[0] - downloaded) / (time.time()  -t)

            # if time.time() - t > 5:
            #     t = t2
            #     downloaded = downloaded2
            #     t2 = time.time()
            #     downloaded2 = self.downloaded[0]

            time.sleep(1.5)

    # Funciones internas
    def __init__(self, url, path, filename=None, headers=[], resume=True, max_connections=10, part_size=2097152, start_from=None):
        # Parametros
        self._resume = resume
        self._path = path
        self._filename = filename
        self._max_connections = max_connections
        self._part_size = part_size
        self._start_from = start_from

        self.states = type('states', (), {"stopped": 0, "connecting": 1, "downloading": 2, "completed": 3, "error": 4})
        self._block_size = 1024 * 100
        self._state = self.states.stopped
        self._write_lock = Lock()
        self._download_lock = Lock()
        self._headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64; rv:50.0) Gecko/20100101 Firefox/50.0"}
        self._speed = 0

        self._threads = [Thread(target=self.__start_part__) for _ in range(self._max_connections)]
        self._speed_thread = Thread(target=self.__speed_metter__)

        # Actualizamos los headers
        self._headers.update(dict(headers))

        # Separamos los headers de la url
        self.__url_to_headers__(url)

        # Obtenemos la info del servidor
        self.__get_download_headers__()

        self._file_size = int(self.response_headers.get("content-length", "0"))

        if not self.response_headers.get("accept-ranges") == "bytes" or self._file_size == 0:
            self._max_connections = 1
            self._part_size = 0
            self._resume = False

        # Obtenemos el nombre del archivo
        self.__get_download_filename__()

        # Abrimos en modo "a+" para que cree el archivo si no existe, luego en modo "r+b" para poder hacer seek()
        self.file = open(os.path.join(self._path, self._filename), "a+")
        self.file = open(os.path.join(self._path, self._filename), "r+b")

        self.__get_download_info__()

    def __url_to_headers__(self, url):
        # Separamos la url de los headers adicionales
        self.url = url.split("|")[0]

        # headers adicionales
        if "|" in url:
            self._headers.update(dict([[header.split("=")[0], urllib.unquote_plus(header.split("=")[1])] for header in url.split("|")[1].split("&")]))

    def __get_download_headers__(self):
        for x in range(3):
            try:
                if not sys.hexversion > 0x0204FFFF:
                    conn = urllib2.urlopen(urllib2.Request(self.url, headers=self._headers))
                    conn.fp._sock.close()
                else:
                    conn = urllib2.urlopen(urllib2.Request(self.url, headers=self._headers), timeout=5)

            except:
                self.response_headers = dict()
                self._state = self.states.error
            else:
                self.response_headers = conn.headers.dict
                self._state = self.states.stopped
                break

    def __get_download_filename__(self):
        # Obtenemos nombre de archivo y extension
        if "filename" in self.response_headers.get("content-disposition",
                                                   "") and "attachment" in self.response_headers.get(
                "content-disposition", ""):
            cd_filename, cd_ext = os.path.splitext(urllib.unquote_plus(
                re.compile("attachment; filename ?= ?[\"|']?([^\"']+)[\"|']?").match(
                    self.response_headers.get("content-disposition")).group(1)))
        if "filename" in self.response_headers.get("content-disposition", "") and "inline" in self.response_headers.get(
                "content-disposition", ""):
            cd_filename, cd_ext = os.path.splitext(urllib.unquote_plus(
                re.compile("inline; filename ?= ?[\"|']?([^\"']+)[\"|']?").match(
                    self.response_headers.get("content-disposition")).group(1)))
        else:
            cd_filename, cd_ext = "", ""

        url_filename, url_ext = os.path.splitext(urllib.unquote_plus(os.path.split(urlparse.urlparse(self.url)[2])[1]))
        if self.response_headers.get("content-type", "application/octet-stream") <> "application/octet-stream":
            mime_ext = mimetypes.guess_extension(self.response_headers.get("content-type"))
        else:
            mime_ext = ""

        # Seleccionamos el nombre mas adecuado
        if cd_filename:
            self.remote_filename = cd_filename
            if not self._filename:
                self._filename = cd_filename

        elif url_filename:
            self.remote_filename = url_filename
            if not self._filename:
                self._filename = url_filename

        # Seleccionamos la extension mas adecuada
        if cd_ext:
            if not cd_ext in self._filename: self._filename += cd_ext
            if self.remote_filename: self.remote_filename += cd_ext
        elif mime_ext:
            if not mime_ext in self._filename: self._filename += mime_ext
            if self.remote_filename: self.remote_filename += mime_ext
        elif url_ext:
            if not url_ext in self._filename: self._filename += url_ext
            if self.remote_filename: self.remote_filename += url_ext

    def __change_units__(self, value):
        import math
        units = ["B", "KB", "MB", "GB"]
        if value <= 0:
            return 0, 0, units[0]
        else:
            return value, value / 1024.0 ** int(math.log(value, 1024)), units[int(math.log(value, 1024))]

    def __get_download_info__(self):
        # Continuamos con una descarga que contiene la info al final del archivo
        self._download_info = {}
        try:
            assert self._resume
            self.file.seek(-16, 2)
            offset = int(self.file.read())
            self.file.seek(offset)
            a = self.file.read()[:-16]
            self._download_info = eval(a)
            assert self._download_info["size"] == self._file_size
            assert self._download_info["url"] == self.url
            self.file.seek(offset)
            self.file.truncate()
            self._start_downloaded = sum([c["current"] - c["start"] for c in self._download_info["parts"]])
            # in-place rotate
            if self._start_from:
                self._download_info["parts"][13:] = sorted(self._download_info["parts"][13:], key=lambda part: part["start"])
                sf = int(self._start_from / 100.0 * (len(self._download_info["parts"]) - 13))
                self._download_info["parts"][13:] = self._download_info["parts"][13:][sf:] + self._download_info["parts"][13:][:sf]
            self.pending_parts = [x for x, a in enumerate(self._download_info["parts"]) if a["status"] != self.states.completed]

        # La info no existe o no es correcta, comenzamos de 0
        except:
            self._download_info["parts"] = []
            if self._file_size and self._part_size:
                for x in range(0, self._file_size, self._part_size):
                    end = x + self._part_size - 1
                    if end >= self._file_size: end = self._file_size - 1
                    self._download_info["parts"].append({"start": x, "end": end, "current": x, "status": self.states.stopped})
                self._download_info["parts"][:] = self._download_info["parts"][-6:] + self._download_info["parts"][:-6]
                # in-place rotate
                if self._start_from:
                    sf = int(self._start_from / 100.0 * (len(self._download_info["parts"]) - 13))
                    self._download_info["parts"][13:] = self._download_info["parts"][13:][sf:] + self._download_info["parts"][13:][:sf]

            else:
                self._download_info["parts"].append(
                    {"start": 0, "end": self._file_size - 1, "current": 0, "status": self.states.stopped})

            self._download_info["size"] = self._file_size
            self._download_info["url"] = self.url
            self._start_downloaded = 0
            self.pending_parts = [x for x in range(len(self._download_info["parts"]))]

            self.file.seek(0)
            self.file.truncate()

    def __open_connection__(self, start, end):
        headers = self._headers.copy()
        if not end: end = ""
        headers.update({"Range": "bytes=%s-%s" % (start, end)})
        if not sys.hexversion > 0x0204FFFF:
            conn = urllib2.urlopen(urllib2.Request(self.url, headers=headers))
        else:
            conn = urllib2.urlopen(urllib2.Request(self.url, headers=headers), timeout=5)
        return conn

    def __start_part__(self):
        while self._state == self.states.downloading:

            self._download_lock.acquire()
            if len(self.pending_parts):
                id = min(self.pending_parts)
                self.pending_parts.remove(id)
                self._download_lock.release()

            # Si no, Termina el thread
            else:

                if len([x for x, a in enumerate(self._download_info["parts"]) if
                        a["status"] in [self.states.downloading, self.states.connecting]]) == 0:
                    self._state = self.states.completed
                    self.file.close()
                self._download_lock.release()
                break

            # Si comprueba si ya está completada, y si lo esta, pasa a la siguiente
            if self._download_info["parts"][id]["current"] > self._download_info["parts"][id]["end"] and \
                            self._download_info["parts"][id]["end"] > -1:
                self._download_info["parts"][id]["status"] = self.states.completed
                continue

            # Marca el estado como conectando
            self._download_info["parts"][id]["status"] = self.states.connecting

            # Intenta la conixion, en caso de error, vuelve a poner la parte en la lista de pendientes
            try:
                connection = self.__open_connection__(self._download_info["parts"][id]["current"],
                                                      self._download_info["parts"][id]["end"])
            except:
                self._download_info["parts"][id]["status"] = self.states.error
                self.pending_parts.append(id)
                time.sleep(5)
                continue

            else:
                self._download_info["parts"][id]["status"] = self.states.downloading

            # Comprobamos que el trozo recibido es el que necesitamos
            if self._download_info["parts"][id]["current"] != int(
                    connection.info().get("content-range", "bytes 0-").split(" ")[1].split("-")[0]):
                self._download_info["parts"][id]["status"] = self.states.error
                self.pending_parts.append(id)
                continue

            while self._state == self.states.downloading:
                try:
                    buffer = connection.read(self._block_size)
                except:
                    self._download_info["parts"][id]["status"] = self.states.error
                    self.pending_parts.append(id)
                    break
                else:

                    if len(buffer):
                        self._write_lock.acquire()
                        self.file.seek(self._download_info["parts"][id]["current"])
                        self.file.write(buffer)
                        self._download_info["parts"][id]["current"] += len(buffer)
                        self._write_lock.release()

                    else:
                        connection.fp._sock.close()
                        self._download_info["parts"][id]["status"] = self.states.completed
                        break

            if self._download_info["parts"][id]["status"] == self.states.downloading:
                self._download_info["parts"][id]["status"] = self.states.stopped


if __name__ == '__main__':
    def signal_term_handler(signal, frame):
        d.stop()
        print "Good bye"

    signal.signal(signal.SIGTERM, signal_term_handler)

    url = sys.argv[1]

    # Lanzamos la descarga
    d = Downloader(url, sys.argv[3], sys.argv[2])
    d.start()

    try:
        # Monitorizamos la descarga hasta que se termine o se cancele
        while d.state == d.states.downloading:
            line1 = "%s" % d.filename
            line2 = "%.2f%% - %.2f %s di %.2f %s a %.2f %s/s (%d/%d)" % (
                d.progress, d.downloaded[1], d.downloaded[2], d.size[1], d.size[2], d.speed[1], d.speed[2],
                d.connections[0],
                d.connections[1])
            line3 = "tempo rimasto: %s" % d.remaining_time
            print (int(d.progress), line1, line2, line3)
            time.sleep(15)

        # Descarga detenida. Obtenemos el estado:
        # Se ha producido un error en la descarga
        if d.state == d.states.error:
            print ("Errore quando si cerca di scaricare %s" % url)
            d.stop()
        # Aun está descargando (se ha hecho click en cancelar)
        elif d.state == d.states.downloading:
            print ("Download fermato")
            d.stop()
        # La descarga ha finalizado
        elif d.state == d.states.completed:
            print ("Scaricato correttamente")
    except KeyboardInterrupt:
        d.stop()
        print "Good bye"
